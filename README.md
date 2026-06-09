# Agent Skills

Personal Agent Skills repository for installation through the open [`skills`](https://www.npmjs.com/package/skills) CLI.

## Install

Each skill installs independently. Use the `--skill` flag with the skill's directory name:

```bash
npx skills add dhanesh/agent-skills --skill <skill-name>
```

For example:

```bash
npx skills add dhanesh/agent-skills --skill crafting-self-prompting-loops
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
```

To install every skill in this repo, omit the `--skill` flag:

```bash
npx skills add dhanesh/agent-skills
```

## Skills

| Skill | Description |
|-------|-------------|
| [`crafting-self-prompting-loops`](crafting-self-prompting-loops/) | Design and build sound self-prompting loops — draft→critique→revise, autonomous task loops, multi-agent orchestration, and human-checkpointed loops. Produces a filled loop spec, a runnable scaffold, and bakes in the mandatory safety properties (hard-stop backstop, trusted/untrusted two-channel boundary). |
| [`tmux-agent-herdr-lite`](tmux-agent-herdr-lite/) | tmux cockpit with Zellij-like human ergonomics and Herdr-like agent supervision — menus, dashboard, pane navigation, blocked/working/done status detection, and jump-to-status navigation. |

See each skill directory's `SKILL.md` for usage and prerequisites.
