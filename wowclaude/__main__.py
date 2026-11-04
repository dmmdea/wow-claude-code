"""Allow `python -m wowclaude` from a bare download of the repository."""

import sys

from wowclaude.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
