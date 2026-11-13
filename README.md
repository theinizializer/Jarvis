# JARVIS Desktop

A Linux AI assistant with a desktop chat, optional voice control, and tools for working with files, the web, and configured services. Use Ollama locally or connect an online AI provider.

> **Privacy:** With Ollama, requests can stay on your computer. When you choose an online provider, conversation text and the results of tools used for a task are sent to that provider. Web browsing requires an internet connection.

## Features

- **Desktop chat** with conversations saved locally and settings organized by category.
- **Local and online models:** Ollama, Groq, Cerebras, NVIDIA, and OpenAI, with configurable fallback.
- **Task tools:** search and read files in your home directory, view system information, run local commands with safeguards and confirmations, search the web, and use memory.
- **Optional integrations:** voice, Gmail, Discord, and local MCP servers.
- **Action safeguards:** sensitive operations require confirmation; existing files are not overwritten without authorization.

Optional integrations may require additional dependencies, credentials, or configuration. See the [installation guide](INSTALLATION.md) and [changelog](CHANGELOG.md) for details.

## Quick start

Requirements: Linux, Git, Python 3 with `venv` and Tk; a graphical desktop environment is needed for the desktop app. On Arch Linux, install at least `git`, `python`, and `tk`.

```sh
git clone https://github.com/theinizializer/Jarvis.git
cd Jarvis
./install.sh --dry-run
./install.sh
jarvis
```

To launch without installing the application-menu command:

```sh
./avvia_jarvis.sh
```

To use text mode:

```sh
./avvia_jarvis.sh --text
```

For detailed requirements and recovery options, see [INSTALLATION.md](INSTALLATION.md).

## Configuration

Open **Settings** in the app to configure AI providers, language, profiles, and integrations. To use an online model, enter an API key for the provider. To use Ollama, install it and make a compatible model available locally.

JARVIS stores configuration and data in your user directories:

- Configuration: `~/.config/jarvis` (or `$XDG_CONFIG_HOME/jarvis`)
- Credentials and data: `~/.local/share/jarvis-data` (or `$XDG_DATA_HOME/jarvis-data`)

Credentials are stored in a `.env` file with restricted permissions, but **are not encrypted**. Protect access to your Linux account and do not share this file.

## Tests

Run the test suite with:

```sh
python3 -m unittest discover -s tests -v
```

Additional checks, including tests that require a graphical environment, are documented alongside the project tests.

## Documentation and versions

- [Installation guide](INSTALLATION.md)
- [Changelog](CHANGELOG.md)
- [Previous version (v9)](https://github.com/theinizializer/Jarvis/tree/old-v9)
- [KyGlove firmware](https://github.com/theinizializer/KyGlove)

JARVIS is distributed under the [MIT License](LICENSE).
