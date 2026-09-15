"""repo-manager: commit reviews, PR triage, and release artifacts as files."""

__version__ = "1.0.1"

# The `pi` release repo-manager is tested against. Consuming workflows install this exact
# version (`npm install -g pi@$(repo-manager pi version)`) so the agent that writes an
# artifact is the one the skills were tuned on.
PI_VERSION = "0.84.2"
