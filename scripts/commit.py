"""Commit and push whatever the previous step changed in the queue folders."""

import sys

from common import commit_and_push

if __name__ == "__main__":
    commit_and_push(sys.argv[1] if len(sys.argv) > 1 else "update queue")
