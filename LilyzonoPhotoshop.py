"""Headless worker launched by the Photoshop tool."""
from app.photoshop import main

if __name__ == "__main__":
    raise SystemExit(main())
