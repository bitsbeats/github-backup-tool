# github-backup-tool

BIG NOTE: This is a work-in-progress.

A crunchy tool for backing up repositories of multiple specified GitHub organizations.

## What does it do?
It will backup all Git repositories it has access to.

## What does it not do?
At this moment, it will not back up:
* Issues
* Comments
* Hooks
* ...

*Eventually*, backing up of these will be implemented.

## How to use?

1. Set up a GitHub token, with a "repo" scope.
2. Configure the `config.yaml` file accordingly.
3. Set up your running environment either via a virtualenv or distribution packages:
   * virtualenv:
       * Create a Python virtual environment via `virtualenv venv`
       * Activate your `venv` via `source venv/bin/activate` (Consider the shell you are using, and adjuct this step accrodingly.)
       * Install all the dependencies via `pip install -r requirements.txt`
   * distribution packages:
      * [GitPython](https://github.com/gitpython-developers/GitPython), [GitPython@Repology](https://repology.org/project/python:gitpython/versions)
      * [PyGithub](https://github.com/PyGithub/PyGithub), [PyGithub@Repology](https://repology.org/project/python:pygithub/versions)
      * [PyYAML](https://pyyaml.org/), [PyYAML@Repology](https://repology.org/project/python:pyyaml/versions)
4. Run `backup.py`.

# TODOs:
- [x] Backup abandoned commits in master as a separate branch if commits were pushed to origin forcefully
- [ ] Backup issues
- [ ] Backup hooks
- [ ] Backup information of users, belonging to an organization
- [ ] Configuration: allow for ignoring of certain repositories
- [ ] Brainstorm about more ideas as to what to back up
