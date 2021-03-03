#!/bin/env python

from datetime import datetime
from git import Repo
from github import Github, Organization, Repository
from typing import List
from yaml import YAMLError, dump, load


try:
    from yaml import CDumper as Dumper
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader, Dumper

import argparse
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

            for org in config["organizations"]:
                if config["organizations"][org]["enabled"]:
                    self.organizations.append(org)

    def get_backup_path(self):
        return self.backup_path

    def get_use_ssh(self):
        return self.clone_via_ssh

    def get_configured_organizations(self):
        self.organizations.sort()
        return self.organizations

    def get_token(self):
        return self.token


class GithubAPI:
    def __init__(self, args, organizations, token):
        self.args = args

        self.github = Github(token)
        self.configured_organizations = organizations
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
            print("WARNING:", "Potential data loss may occur.", file=sys.stderr)

        if self.args.verbose:
            print("Repositories that will be backed up:")


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

    def get_all_repositories_urls(self, repositories: List[Repository.Repository], use_ssh=True):
        repository_urls = []

        for repository in repositories:
            repository_urls.append(repository.ssh_url if use_ssh else repository.clone_url)

        return repository_urls


class GitRepo:
    def __init__(self, args, backup_path, github: GithubAPI, ssh_key):
        self.backup_path = backup_path
        self.ssh_key = ssh_key
        self.github = github

    def print_repos(self):
        for organization in self.github.get_github_configured_organizations():
            print(organization.login + ":")
            #repositories.extend(self.github.get_all_repositories_urls(self.github.get_all_repositories_in_organization(organization)))
            print('\n'.join('  {}: {}'.format(*k) for k in enumerate(
                self.github.get_all_repositories_urls(self.github.get_all_repositories_in_organization(organization)))))

class GithubBackup:
    def __init__(self, args, backup_path, organizations, token):
        self.backup_path = backup_path
        self.organizations = organizations
        self.token = token
        self.repos = []

    def get_token(self):
        return self.token

    def get_backup_path(self):
        return self.backup_path

    def append_repo(self, repo_url):
        self.repos.append(repo_url)


def main():
    args = parse_args()

    if args.config:
        config = Configuration(args.config)
    else:
        config = Configuration("config.yaml")

    github_backup = GithubBackup(args, config.get_backup_path(), config.get_configured_organizations(),
                                 config.get_token())

    github_api = GithubAPI(args, config.get_configured_organizations(), github_backup.get_token())
    github_api.print_info()

    git = GitRepo(args, config.get_backup_path(), github_api, None)
    git.print_repos()

if __name__ == '__main__':
    main()
