# github-backup-tool

BIG NOTE: This is a work-in-progress.

A crunchy tool for backing up repositories of multiple specified GitHub organizations.

## What does it do?
It will backup all Git repositories it has access to. If no GitHub token is provided, only publicly accessible repositories will be cloned.

## What does it not do?
At this moment, it will not back up:
* Issues
* Comments
* Hooks
* ...

Eventually, backing up of these will be implemented.

## How to use?

1. Set up a GitHub token, with a "repo" scope.
2. Configure the `config.yaml` file accordingly.
3. Set up your running environment:
    1. Create a Python virtual environment via `virtualenv venv`
    2. Activate your `venv` via `source venv/bin/activate` (Consider the shell you are using, and adjuct this step accrodingly.)
    3. Install all the dependencies via `pip install -r requirements.txt`
4. Run `github-backup.py`.

## LICENSE: MIT
That is, it comes without any warranty, so no sticker that warns you that the warranty will be voided if destroyed.
