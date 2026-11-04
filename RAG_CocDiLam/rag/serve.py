"""Prepare the restored volume's ownership, then run the gateway as app."""

import os
import pwd
from pathlib import Path


def main():
    account = pwd.getpwnam("app")
    if os.getuid() == 0:
        # The original standalone image used UID 10002; this image uses 10001.
        root = Path("/data/rag-admin")
        root.mkdir(parents=True, exist_ok=True)
        for directory, subdirs, filenames in os.walk(root, followlinks=False):
            for target in [Path(directory), *(Path(directory) / name for name in subdirs + filenames)]:
                info = target.lstat()
                if (info.st_uid, info.st_gid) != (account.pw_uid, account.pw_gid):
                    os.chown(target, account.pw_uid, account.pw_gid, follow_symlinks=False)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    os.execvp("uvicorn", ["uvicorn", "rag.gateway:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"])


if __name__ == "__main__":
    main()
