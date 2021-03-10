from datetime import datetime, timezone
from pathlib import Path
from typing import List

from git import cmd, exc, Repo
from github import Github, Repository
from humanize import naturaldelta
from yaml import YAMLError, load

try:
    from yaml import CDumper as Dumper
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader, Dumper

import os
import shutil
import sys
import time

from .repository_db import Tracker


class Configuration:
    def __init__(self, filename):
        with open(filename, "r") as configfile:
            try:
                config = load(configfile, Loader=Loader)
            except YAMLError as err:
                if hasattr(err, 'problem_mark'):
                    mark = err.problem_mark
                    print("Error in configuration on line %s" %
                          mark.line, file=sys.stderr)
                    print("Check your configuration!", file=sys.stderr)
                    sys.exit(1)

            self.backup_path = config["default"]["backupPath"]
            self.clone_via_ssh = config["default"]["cloneViaSSH"]
            self.organizations = []
            self.token = config["default"]["token"]

            ssh_key_path = Path(config["default"]["ssh-key"] if config["default"]["ssh-key"] is not None else "")
            self.ssh_key = ssh_key_path if os.path.exists(ssh_key_path) else None

            for org in config["organizations"]:
                if config["organizations"][org]["enabled"]:
                    self.organizations.append(org)

            self.track_db = config["tracker"]["trackDB"]
            self.track_repositories = config["tracker"]["trackRepositories"]
            self.track_abandoned_branches = config["tracker"]["trackAbandonedBranches"]
            self.delete_abandoned_branches_after = config["tracker"]["deleteAbandonedBranchesAfter"]
            self.delete_removed_repositories_after = config["tracker"]["deleteRemovedRepositoriesAfter"]
            self.delete_removed_branches_after = config["tracker"]["deleteRemovedBranchesAfter"]

    def get_backup_path(self):
        return self.backup_path

    def get_use_ssh(self):
        return self.clone_via_ssh

    def get_ssh_key(self):
        return Path(self.ssh_key)

    def get_configured_organizations(self):
        self.organizations.sort()
        return self.organizations

    def get_token(self):
        return self.token

    @classmethod
    def get_days(cls, time_period):
        if "d" in time_period:
            return int(str(time_period).strip("d"))
        elif "m" in time_period:
            return int(str(time_period).strip("m")) * 30
        elif "y" in time_period:
            return int(str(time_period).strip("y")) * 365
        else:
            return -1

    def get_delete_abandoned_branches_after(self):
        duration = self.delete_abandoned_branches_after
        return self.get_days(duration)

    def get_track_db(self):
        return self.track_db

    def get_track_repositories(self):
        return self.track_repositories

    def get_track_abandoned_branches(self):
        return self.track_abandoned_branches

    def get_delete_removed_repositories_after(self):
        duration = self.delete_removed_repositories_after
        return self.get_days(duration)

    def get_delete_removed_branches_after(self):
        duration = self.delete_removed_branches_after
        return self.get_days(duration)


class GithubApi:
    def __init__(self, config: Configuration):
        self.config = config

        self.github = Github(config.get_token())
        self.configured_organizations = config.get_configured_organizations()
        self.github_organizations = []

    def print_info(self):
        now = datetime.now()
        user = self.github.get_user()

        print("Accessing GitHub as", user.login, "on", now.strftime("%d.%m.%Y %H:%M:%S"), "\n")

        github_organizations = []

        for organization in self.get_github_organizations():
            github_organizations.append(organization.login)

        organizations_difference = [x for x in github_organizations if x not in self.configured_organizations]

        print("Organizations selected for backup:")
        for organization in self.configured_organizations:
            print("  ", organization, "\n" if organization == self.configured_organizations[-1] else "")

        if len(organizations_difference) > 0:
            print("Organization"
                  + ("s" if len(organizations_difference) > 1 else "")
                  + " NOT selected for backup:")

            for organization in organizations_difference:
                print("  ", organization, "\n" if organization == organizations_difference[-1] else "")

            print("WARNING:", "There are organizations not selected for backup.", file=sys.stderr)
            print("WARNING:", "Potential data loss may occur.", "\n", file=sys.stderr)

    def get_github_organizations(self):
        self.github_organizations = self.github.get_user().get_orgs()
        return self.github_organizations

    def get_github_configured_organizations(self):
        organizations = []

        for configured_organization in self.configured_organizations:
            for organization in self.github.get_user().get_orgs():
                if configured_organization == organization.login:
                    organizations.append(organization)

        return organizations

    def get_all_repositories_in_organization(self, organization):
        repositories_in_organization = []

        for repository in self.github.get_organization(organization.login).get_repos():
            repositories_in_organization.append(repository)

        return repositories_in_organization

    @classmethod
    def get_all_repositories_urls(cls, repositories: List[Repository.Repository], use_ssh=True):
        repository_urls = []

        for repository in repositories:
            repository_urls.append(repository.ssh_url if use_ssh else repository.clone_url)

        return repository_urls

    def rate_limit_wait(self):
        limit = self.github.get_rate_limit()
        reset_core = limit.core.reset.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        requests_remaining = self.github.rate_limiting[0]
        request_limit = self.github.rate_limiting[1]

        if (request_limit - requests_remaining) <= 0:
            print("GitHub Rate Limit exceeded!", "Waiting for", naturaldelta(reset_core - now), "for reset.")
            wait_time = (reset_core - now).total_seconds()
            time.sleep(wait_time)


class Git:
    def __init__(self, config: Configuration, github: GithubApi, ssh_key):
        self.backup_path = config.get_backup_path()
        self.config = config
        self.ssh_key = ssh_key
        self.github = github

    def print_repos(self):
        for organization in self.github.get_github_configured_organizations():
            print(organization.login + ":")
            print('\n'.join('  {}: {}'.format(*k) for k in enumerate(
                self.github.get_all_repositories_urls(self.github.get_all_repositories_in_organization(organization),
                                                      self.config.get_use_ssh()))))

    def check_clone_exists(self, repository: Repository):
        path = os.path.join(Path(self.config.get_backup_path()), Path(repository.full_name))
        if not os.path.exists(path):
            return False
        else:
            return True

    def check_remote_repository_initialized(self, repository: Repository):
        repository_url = self.get_repository_url(repository)
        git = cmd.Git()

        try:
            git.ls_remote('-h', repository_url)
            return True
        except exc.GitCommandError as error:
            print(repository.full_name, "empty remote/access restricted:", error.status, file=sys.stderr)
            return False

    def clone(self, repository: Repository):
        repository_path = self.get_repository_path(repository)
        repository_url = self.get_repository_url(repository)

        local_clone = Repo()

        git_ssh_cmd = {}
        ssh_str_path = str(self.config.get_ssh_key())

        if ssh_str_path != ".":
            ssh_cmd = "ssh -i " + ssh_str_path + " -F " + "/dev/null " + "-o StrictHostKeyChecking=accept-new"
            git_ssh_cmd = {
                "GIT_SSH_COMMAND": ssh_cmd}

        try:
            print(repository.full_name, "->", repository_path)
            local_clone.clone_from(repository_url, repository_path, env=git_ssh_cmd)

            tracker = Tracker(self.config)
            tracker.track_repository(repository.full_name, repository.organization.login, datetime.now(),
                                     datetime.now(), True)

        except exc.GitCommandError as error:
            print(repository.full_name, "cannot access repository:", error.status, file=sys.stderr)

    def update(self, repository):
        if not self.check_remote_repository_initialized(repository):
            return

        if self.check_clone_exists(repository):
            self.update_local(repository)
        else:
            self.clone(repository)

    def get_repository_path(self, repository: Repository):
        return os.path.join(Path(self.config.get_backup_path()), Path(repository.full_name))

    def get_repository_url(self, repository: Repository):
        return repository.ssh_url if self.config.get_use_ssh() else repository.clone_url

    def checkout_abandoned_branch(self, repository: Repository.Repository, default_branch, timestamp):
        repo = Repo(self.get_repository_path(repository))
        branch_name = str(repo.active_branch) + "_abandoned_" + timestamp.strftime("%Y%m%d_%H%M%S")

        repo.git.checkout('-b', branch_name)
        repo.git.checkout(default_branch)

        now = datetime.now()
        tracker = Tracker(self.config)
        tracker.track_branch(branch_name, repository.full_name, now, now, True, True)

    @classmethod
    def reset_branch(cls, repo: Repo, branch):
        ref_to_reset = ""

        for ref in repo.remote().refs:
            if str(ref).rsplit('/', 1)[-1] == str(branch):
                ref_to_reset = ref

        repo.git.reset('--hard', str(ref_to_reset))

    def remote_branch_exists(self, repository: Repo, branch_name):
        for branch in self.get_remote_branches(repository):
            if branch_name in branch:
                return True

        return False

    @classmethod
    def get_remote_branches(cls, repository: Repo):
        remote_branches = []

        for ref in repository.remote().refs:
            if str(ref).rsplit('/', 1)[-1] != "HEAD":
                remote_branches.append(str(ref))

        return remote_branches

    def get_remote_branches_commit_ids(self, repository: Repo):
        branches_commit_ids = []

        for commit_id in self.get_remote_branches(repository):
            branches_commit_ids.append(str(repository.rev_parse(commit_id)))

        return branches_commit_ids

    @classmethod
    def get_local_branches(cls, repository: Repo):
        local_branches = []

        for branch in repository.branches:
            local_branches.append(str(branch).rsplit('/', 1)[-1])

        return local_branches

    def get_local_branches_commit_ids(self, repository: Repo):
        branches_commit_ids = []

        for commit_id in self.get_local_branches(repository):
            branches_commit_ids.append(str(repository.rev_parse(commit_id)))

        return branches_commit_ids

    def backup_branch(self, repository: Repository.Repository, default_branch):
        repo = Repo(self.get_repository_path(repository))
        timestamp = datetime.now()

        commit_id = repo.head.object.hexsha

        if commit_id not in self.get_remote_branches_commit_ids(repo):
            print(repository.full_name, str(default_branch), "->",
                  str(default_branch) + "_abandoned_" + timestamp.strftime("%Y%m%d_%H%M%S"))
            self.checkout_abandoned_branch(repository, default_branch, timestamp)

            print(repository.full_name, str(default_branch), "<-", "origin/" + default_branch)
            self.reset_branch(repo, default_branch)

    def update_local(self, repository):
        repo = Repo(self.get_repository_path(repository))
        local_default_branch = repo.active_branch

        print(repository.full_name, "<-", "remote")

        try:
            repo.remote().fetch()
        except exc.GitCommandError as error:
            pass

        tracker = Tracker(self.config)

        tracker.track_repository(repository.full_name, repository.organization.login, datetime.now(),
                                 datetime.now(), True)

        for branch in self.get_remote_branches(repo):
            branch_name = str(branch).rsplit('/', 1)[-1]

            try:
                if local_default_branch != branch_name:
                    repo.git.checkout('-b', branch_name, str(branch))
                    tracker.track_branch(branch_name, repository.full_name, datetime.now(), datetime.now(), False, True)
            except exc.GitCommandError:
                pass

            try:
                repo.git.merge('--ff-only')
                print(repository.full_name, branch_name, "<-", "origin/" + branch_name)

                tracker.update_branch(branch_name, repository.full_name, datetime.now())
            except exc.GitCommandError as error:
                if error.status == 128:
                    print(repository.full_name, branch_name, "<-", "origin/" + branch_name, "failed", file=sys.stderr)
                    self.backup_branch(repository, branch_name)

        repo.git.checkout(str(local_default_branch))

        tracker.update_repository(repository.full_name, repository.organization.login, datetime.now())

    def remove_branch(self, branch_name, repository_name):
        repo = Repo(os.path.join(Path(self.config.get_backup_path()), Path(repository_name)))

        removed = False

        if "_abandoned_" in branch_name:
            try:
                repo.git.branch('-D', branch_name)
                removed = True
            except exc.GitCommandError as error:
                removed = False
        else:
            remote_branches = []

            for ref in repo.remote().refs:
                if str(ref).rsplit('/', 1)[-1] == "HEAD":
                    remote_branches.append(str(ref))

            for branch in remote_branches:
                try:
                    if branch != branch_name:
                        repo.git.branch('-D', branch_name)
                        removed = True
                except exc.GitCommandError as error:
                    removed = False

        return removed


class GithubBackup:
    def __init__(self, config: Configuration):
        self.config = config
        self.github_api = GithubApi(config)
        self.tracker = Tracker(self.config)

        if self.config.get_ssh_key() is None:
            print("SSH Key not found.", file=sys.stderr)

        self.git = Git(config, self.github_api, self.config.get_ssh_key())

        self.github_api.print_info()

    def backup_organizations(self):
        print("-" * 80)
        print("Backing up...")
        total_organizations = 0
        total_repositories = 0
        for organization in self.github_api.get_github_configured_organizations():
            total_organizations += 1
            repositories = 0

            for repository in self.github_api.get_all_repositories_in_organization(organization):
                self.github_api.rate_limit_wait()

                total_repositories += 1
                repositories += 1

                self.git.update(repository)

            print("-" * 80)
            print("Processed", repositories, "repositories in", organization.login)
            print("-" * 80)

        print("Processed", total_repositories, "repositories in", total_organizations, "organizations.")

    def clean_abandoned_branches(self):
        retention_period = self.config.get_delete_abandoned_branches_after()

        branches = self.tracker.get_abandoned_branches_older_than(retention_period)

        if len(branches) == 0:
            return

        print("-" * 80)
        print("Cleaning up abandoned branches:")

        for branch_tuple in branches:
            removed = self.git.remove_branch(branch_tuple[0], branch_tuple[1])
            if removed:
                print(branch_tuple[1], branch_tuple[0], "removed")
                self.tracker.delete_branch(branch_tuple[0], branch_tuple[1])

    def clean_tracked_branches(self):
        retention_period = self.config.get_delete_removed_branches_after()

        branches = self.tracker.get_branches_older_than(retention_period)

        if len(branches) == 0:
            return

        print("-" * 80)
        print("Cleaning up branches:")

        for branch_tuple in branches:
            removed = self.git.remove_branch(branch_tuple[0], branch_tuple[1])
            if removed:
                print(branch_tuple[1], branch_tuple[0], "removed")
                self.tracker.delete_branch(branch_tuple[0], branch_tuple[1])

    def clean_tracked_repositories(self):
        retention_period = self.config.get_delete_removed_repositories_after()

        repositories = self.tracker.get_repositories_older_than(retention_period)

        if len(repositories) == 0:
            return

        print("-" * 80)
        print("Cleaning up repositories:")

        for repository in repositories:
            shutil.rmtree(os.path.join(self.config.get_backup_path(), repository), ignore_errors=True)
            self.tracker.delete_repository(repository, repository.rsplit('/', 1)[0])
            print(repository, "removed")
