#!/usr/bin/env python3

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

import argparse
import os
import sys
import time


def parse_args():
    parser = argparse.ArgumentParser(description='Backup GitHub Repositories')
    parser.add_argument('-c',
                        '--config',
                        dest='config',
                        help='configuration file to use')
    return parser.parse_args()


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
            self.delete_abandoned_branches_after = config["default"]["deleteAbandonedBranchesAfter"]
            self.organizations = []
            self.token = config["default"]["token"]
            self.verbose = config["default"]["verbose"]

            ssh_key_path = Path(config["default"]["ssh-key"] if config["default"]["ssh-key"] is not None else "")
            self.ssh_key = ssh_key_path if os.path.exists(ssh_key_path) else None

            for org in config["organizations"]:
                if config["organizations"][org]["enabled"]:
                    self.organizations.append(org)

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

    def get_verbose(self):
        return self.verbose

    def get_retention_days(self):
        duration = self.delete_abandoned_branches_after
        if "d" in duration:
            return int(str(duration).strip("d"))
        elif "m" in duration:
            return int(str(duration).strip("m")) * 30
        elif "y" in duration:
            return int(str(duration).strip("y")) * 365
        else:
            print("Check deleteAbandonedBranchesAfter in your configuration file!")
            return -1


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

    def clone_mirror(self, repository: Repository):
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
        except exc.GitCommandError as error:
            print(repository.full_name, "cannot access repository:", error.status, file=sys.stderr)

    def update(self, repository):
        if not self.check_remote_repository_initialized(repository):
            return

        if self.check_clone_exists(repository):
            self.update_local(repository)
        else:
            self.clone_mirror(repository)

    def get_repository_path(self, repository: Repository):
        return os.path.join(Path(self.config.get_backup_path()), Path(repository.full_name))

    def get_repository_url(self, repository: Repository):
        return repository.ssh_url if self.config.get_use_ssh() else repository.clone_url

    @classmethod
    def checkout_abandoned_branch(cls, repository: Repo, default_branch, timestamp):
        branch_name = str(repository.active_branch) + "_abandoned_" + timestamp.strftime("%Y%m%d_%H%M%S")

        repository.git.checkout('-b', branch_name)
        repository.git.checkout(default_branch)

    @classmethod
    def reset_branch(cls, repo: Repo, branch):
        ref_to_reset = ""

        for ref in repo.remote().refs:
            if str(ref).rsplit('/', 1)[-1] == str(branch):
                ref_to_reset = ref

        repo.git.reset('--hard', str(ref_to_reset))

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

    @classmethod
    def get_abandoned_branch_creation_date(cls, branch_name: str):
        if "_abandoned_" in branch_name:
            branch_name_splitted = branch_name.split("_")
            date_time = branch_name_splitted[-2] + "_" + branch_name_splitted[-1]
            date_time_obj = datetime.strptime(date_time, '%Y%m%d_%H%M%S')
            return date_time_obj
        else:
            return -1

    def backup_branch(self, repository: Repository.Repository, default_branch):
        repo = Repo(self.get_repository_path(repository))
        timestamp = datetime.now()

        commit_id = repo.head.object.hexsha

        if commit_id not in self.get_remote_branches_commit_ids(repo):
            print(repository.full_name, str(default_branch), "->",
                  str(default_branch) + "_abandoned_" + timestamp.strftime("%Y%m%d_%H%M%S"))
            self.checkout_abandoned_branch(repo, default_branch, timestamp)

            print(repository.full_name, str(default_branch), "<-", "origin/" + default_branch)
            self.reset_branch(repo, default_branch)

    def update_local(self, repository):
        repo = Repo(self.get_repository_path(repository))
        local_default_branch = repo.active_branch

        print(repository.full_name, "<-", "remote")
        repo.remote().fetch()

        for branch in self.get_remote_branches(repo):
            branch_name = str(branch).rsplit('/', 1)[-1]
            try:
                if local_default_branch != branch_name:
                    repo.git.checkout('-b', branch_name, str(branch))
            except exc.GitCommandError:
                pass

            try:
                repo.git.merge('--ff-only')
                print(repository.full_name, branch_name, "<-", "origin/" + branch_name)
            except exc.GitCommandError as error:
                if error.status == 128:
                    print(repository.full_name, branch_name, "<-", "origin/" + branch_name, "failed", file=sys.stderr)
                    self.backup_branch(repository, branch_name)

        repo.git.checkout(str(local_default_branch))

    def clean(self, repository: Repository):
        if not self.check_clone_exists(repository):
            return []

        repo = Repo(self.get_repository_path(repository))
        removed_branches = []
        timestamp = datetime.now()
        for branch in self.get_local_branches(repo):
            if "_abandoned_" not in branch:
                continue

            retention_days = self.config.get_retention_days()

            if retention_days < 0:
                continue

            if (timestamp - self.get_abandoned_branch_creation_date(branch)).days >= retention_days:
                repo = Repo(self.get_repository_path(repository))
                print(repository.full_name, "removing", branch, "older than", retention_days, "days")
                repo.git.branch('-D', branch)
                removed_branches.append(branch)

        return removed_branches


class GithubBackup:
    def __init__(self, config: Configuration):
        self.config = config
        self.github_api = GithubApi(config)

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
        print("-" * 80 + "\n")

    def clean_abandoned_branches(self):
        print("-" * 80)
        print("Cleaning up...")

        total_organizations = 0
        total_repositories = 0
        absolute_removed_branches = 0
        for organization in self.github_api.get_github_configured_organizations():
            total_organizations += 1
            repositories = 0
            total_removed_branches = 0

            for repository in self.github_api.get_all_repositories_in_organization(organization):
                total_repositories += 1
                repositories += 1
                removed_branches = self.git.clean(repository)
                total_removed_branches += len(removed_branches)

                if len(removed_branches) > 0:
                    print(repository.full_name, "removed:")
                    print(*removed_branches, sep="\n")

            print("-" * 80)
            repos = repositories > 1 or repositories == 0
            print("Processed", repositories, "repositor" + ("ies" if repos else "y"), organization.login)

            branches = total_removed_branches > 1 or total_removed_branches == 0
            print("Removed", total_removed_branches, "branch" + ("es" if branches else ""))

            absolute_removed_branches += total_removed_branches

        print("-" * 80)

        repos = total_repositories > 1 or total_repositories == 0
        orgas = total_organizations > 1 or total_organizations == 0
        print("Processed", total_repositories, "repositor" + ("ies" if repos else "y"), "in",
              total_organizations, "organization" + ("s" if orgas else ""))

        branches = absolute_removed_branches > 1 or absolute_removed_branches == 0
        print("Removed", absolute_removed_branches, "branch" + ("es" if branches else ""))
        print("-" * 80)

    def clean_tracked_repositories(self):
        pass


def main():
    now = datetime.now()
    print("GitHub Backup Tool", now.strftime("%d.%m.%Y %H:%M:%S"), "\n")
    print("Copyright (c) 2021 Thomann Bits & Beats GmbH")
    print("All Rights Reserved.")
    print("~" * 80, "\n")

    args = parse_args()

    if args.config:
        config = Configuration(args.config)
    else:
        config = Configuration("config.yaml")

    backup = GithubBackup(config)
    backup.backup_organizations()
    backup.clean_abandoned_branches()

    end = datetime.now()
    print("Backup ended:", end.strftime("%d.%m.%Y %H:%M:%S"), "Duration:", naturaldelta(now - end))


if __name__ == '__main__':
    main()
