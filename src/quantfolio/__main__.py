"""Allow ``python -m quantfolio`` as well as the installed console entry point."""

from .cli import main

if __name__ == "__main__":
    main()
