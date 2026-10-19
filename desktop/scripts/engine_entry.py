"""PyInstaller entry; HoloQA tools run in this process, not a frozen subprocess."""
from holoqa.desktop import main

if __name__ == "__main__":
    main()
