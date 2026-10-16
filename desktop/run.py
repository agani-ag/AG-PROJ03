"""The entry point PyInstaller freezes. Everything real is in the package."""
import sys

from j6desk.__main__ import main

if __name__ == '__main__':
    sys.exit(main())
