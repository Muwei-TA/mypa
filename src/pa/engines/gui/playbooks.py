"""Three desktop playbooks (M4-04, PRD §8.5).

Each desktop app gets a playbook yaml under the config dir. These mirror
the §8.4 step vocabulary: open-only dry-runs must pass without submitting
anything.
"""

import textwrap

CODEX_DESKTOP = textwrap.dedent(
    """\
    app: "Codex"
    version_hint: ">=0.1"
    steps:
      - activate_app: {}
      - new_session: {hotkey: "cmd+n"}
      - wait_for: {ax_role: "AXTextArea", timeout_s: 10}
      - paste_text: {from: "prompt"}
      - press: {key: "return"}
      - wait_until_idle: {indicator: {ax_title_absent: "停止生成"}, timeout_s: 600}
      - copy_last_reply: {strategy: "ax_value"}
      - screenshot: {save_as: "final"}
    result:
      extractor: text
    """
)

DOUBAO_DESKTOP = textwrap.dedent(
    """\
    app: "豆包"
    version_hint: ">=1.0"
    steps:
      - activate_app: {}
      - new_session: {hotkey: "cmd+n"}
      - wait_for: {ax_role: "AXTextArea", timeout_s: 10}
      - paste_text: {from: "prompt"}
      - press: {key: "return"}
      - wait_until_idle: {indicator: {ax_title_absent: "停止生成"}, timeout_s: 600}
      - copy_last_reply: {strategy: "ax_value | hotkey:cmd+shift+c | screenshot_ocr"}
      - screenshot: {save_as: "final"}
    result:
      extractor: text
    """
)

WORKBUDDY_DESKTOP = textwrap.dedent(
    """\
    app: "WorkBuddy"
    version_hint: ">=1.0"
    steps:
      - activate_app: {}
      - new_session: {hotkey: "cmd+n"}
      - wait_for: {ax_role: "AXTextArea", timeout_s: 10}
      - paste_text: {from: "prompt"}
      - press: {key: "return"}
      - wait_until_idle: {indicator: {ax_title_absent: "停止生成"}, timeout_s: 600}
      - copy_last_reply: {strategy: "ax_value"}
      - screenshot: {save_as: "final"}
    result:
      extractor: text
    """
)

PLAYBOOKS = {
    "codex_desktop": CODEX_DESKTOP,
    "doubao_desktop": DOUBAO_DESKTOP,
    "workbuddy_desktop": WORKBUDDY_DESKTOP,
}


def install_playbooks(config_dir) -> dict:
    """Write the three desktop playbooks under <config_dir>/playbooks/."""
    import yaml

    target = config_dir / "playbooks"
    target.mkdir(parents=True, exist_ok=True)
    written = {}
    for engine_id, content in PLAYBOOKS.items():
        path = target / f"{engine_id}.yaml"
        # round-trip so files are normalized yaml, not embedded prose
        data = yaml.safe_load(content)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        written[engine_id] = str(path)
    return written
