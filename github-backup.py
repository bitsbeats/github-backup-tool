#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path
from typing import List

from git import cmd, exc, Repo
from github import Github, Repository
from yaml import YAMLError, load

try:
    from yaml import CDumper as Dumper
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader, Dumper

import argparse
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(description='Backup GitHub Repositories')
    parser.add_argument('-c',
                        '--config',
                        dest='config',
                        help='configuration file to use')
    parser.add_argument('-k',
                        '--sshkey',
                        dest='ssh_key',
                        help='ssh private key to use')
    parser.add_argument('-v',
                        '--verbose',
                        dest='verbose',
                        help='be verbose')
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


class GithubApi:
    def __init__(self, config: Configuration):
        self.config = config

        self.github = Github(config.get_token())
        self.configured_organizations = config.get_configured_organizations()
        self.github_organizations = []

        self.verbose = config.get_verbose()

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
            print(repository.full_name, "Local clone not present", )
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
            print(repository.full_name, "Remote not initialized or access restricted. Git status code:", error.status,
                  file=sys.stderr)
            return False

    def print_backup_paths(self):
        repositories = []

        for organization in self.github.get_github_configured_organizations():
            repositories.extend(self.github.get_all_repositories_in_organization(organization))

        for repository in repositories:
            self.check_clone_exists(repository)
            self.check_remote_repository_initialized(repository)

    def clone_mirror(self, repository: Repository.Repository):
        repository_path = self.get_repository_path(repository)
        repository_url = self.get_repository_url(repository)

        local_clone = Repo()

        git_ssh_cmd = {}
        ssh_str_path = str(self.config.get_ssh_key())

        if ssh_str_path != ".":
            git_ssh_cmd = {
                "GIT_SSH_COMMAND": "ssh -i " + ssh_str_path + " -F " + "/dev/null " + "-o StrictHostKeyChecking=accept-new"}

        try:
            print(repository.full_name, "initial cloning to", repository_path, "via",
                  "SSH" if self.config.get_use_ssh() else "HTTPS")
            print("  ", "from", repository_url)
            local_clone.clone_from(repository_url, repository_path, env=git_ssh_cmd)
        except exc.GitCommandError as error:
            print(repository.full_name, "cannot access repository. Git status code:", error.status,
                  file=sys.stderr)

    def update(self, repository):
        if not self.check_remote_repository_initialized(repository):
            return

        if self.check_clone_exists(repository):
            self.update_local(repository)
        else:
            self.clone_mirror(repository)

    def get_repository_path(self, repository: Repository.Repository):
        return os.path.join(Path(self.config.get_backup_path()), Path(repository.full_name))

    def get_repository_url(self, repository: Repository.Repository):
        return repository.ssh_url if self.config.get_use_ssh() else repository.clone_url

    @classmethod
    def checkout_abandoned_branch(cls, repository: Repo, default_branch, timestamp):
        branch_name = str(repository.active_branch) + "_abandoned_" + timestamp.strftime("%d%m%y_%H%M%S")

        repository.git.checkout('-b', branch_name)
        repository.git.checkout(default_branch)

    @classmethod
    def reset_default_branch(cls, repository: Repo, default_branch):
        ref_to_reset = ""

        for ref in repository.remote().refs:
            if str(ref).rsplit('/', 1)[-1] == str(default_branch):
                ref_to_reset = ref

        repository.git.reset('--hard', ref_to_reset)

    def update_local(self, repository):
        repo = Repo(self.get_repository_path(repository))
        local_default_branch = repo.active_branch

        try:
            repo.remote().pull('--ff-only')
            print(repository.full_name, "successfully updated")
        except exc.GitCommandError as error:
            if error.status == 128:
                print(repository.full_name, "cannot safely pull. Git status code:", error.status, file=sys.stderr)

                timestamp = datetime.now()

                print(repository.full_name, "backing local branch to",
                      str(local_default_branch) + "_abandoned_" + timestamp.strftime("%d%m%y_%H%M%S"))
                self.checkout_abandoned_branch(repo, local_default_branch, timestamp)

                print(repository.full_name, "fetch from remote")
                repo.remote().fetch()

                print(repository.full_name, "resetting", str(local_default_branch), "branch hard")
                self.reset_default_branch(repo, local_default_branch)


class GithubBackup:
    def __init__(self, config: Configuration):
        self.config = config
        self.github_api = GithubApi(config)

        if self.config.get_ssh_key() is None:
            print("SSH Key not found.", file=sys.stderr)

        self.git = Git(config, self.github_api, self.config.get_ssh_key())

        self.github_api.print_info()

    def backup_organizations(self):
        for organization in self.github_api.get_github_configured_organizations():
            for repository in self.github_api.get_all_repositories_in_organization(organization):
                self.git.update(repository)


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


if __name__ == '__main__':
    main()
