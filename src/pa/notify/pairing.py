import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from pa.common import PAError
from pa.config.loader import load
from pa.config.setup import save_files
from pa.effects.store import enqueue
from pa.store.db import transaction


def create_pairing(config_dir):
    config = load(config_dir)
    if config.principals.owner.feishu_open_id:
        raise PAError(
            "Feishu owner is already paired; changing owner requires explicit reconfiguration"
        )
    code = secrets.token_urlsafe(18)
    path = config.main.data_path(config_dir) / "feishu-pairing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "hash": hashlib.sha256(code.encode()).hexdigest(),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            }
        )
    )
    path.chmod(0o600)
    return code


def try_pair(data, config_dir, facade):
    config = facade.config
    if config.principals.owner.feishu_open_id:
        return False
    body = data["event"]
    message = body["message"]
    if message.get("chat_type") != "p2p" or body["sender"].get("sender_type") != "user":
        return False
    content = json.loads(message.get("content", "{}"))
    text = content.get("text", "")
    if not text.startswith("pa pair "):
        return False
    path = config.main.data_path(config_dir) / "feishu-pairing.json"
    # The SQLite writer lock serializes the one-time claim across receiver processes.
    with transaction(facade.engine) as c:
        if not path.is_file():
            return False
        stored = json.loads(path.read_text())
        if datetime.now(UTC) >= datetime.fromisoformat(stored["expires_at"]):
            return False
        digest = hashlib.sha256(text[len("pa pair ") :].strip().encode()).hexdigest()
        if not secrets.compare_digest(digest, stored["hash"]):
            return False
        actor = body["sender"]["sender_id"]["open_id"]
        principals = config.principals.model_dump(mode="json")
        principals["owner"]["feishu_open_id"] = actor
        save_files(
            config_dir,
            {
                "principals.yaml": principals,
                "notify.yaml": {"target": {"type": "user", "id": actor}},
            },
        )
        path.unlink()
        enqueue(
            c,
            kind="feishu.send",
            idem_key="pair:" + message["message_id"],
            payload={
                "receive_id_type": "open_id",
                "receive_id": actor,
                "content": {"text": "配对成功。现在可以直接发文本任务给我。"},
            },
            order_key="feishu:" + actor,
        )
    return True
