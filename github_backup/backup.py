from datetime import datetime, timezone
from distutils.version import LooseVersion
from pathlib import Path
from typing import List

from git import cmd, exc, Repo
from github import Github, Repository
from humanize import naturaldelta
from stat import *
from yaml import YAMLError, load

try:
    from yaml import CDumper as Dumper
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader, Dumper

import logging
import os
import shutil
import sys
import time

from github_backup.db import Tracker


class Configuration:
    def __init__(self, filename):
        self.error_count = 0
        self.start_datetime = datetime.now()
        self.end_datetime = None

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

            try:
                os.mkdir(os.path.abspath(self.backup_path))
            except OSError:
                pass

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
            self.warn_before_repository_deletion = config["tracker"]["warnBeforeRepositoryDeletion"]
            self.warn_before_orphaned_org = config["tracker"]["warnBeforeOrphanedOrganizationDeletion"]
            self.delete_orphaned_org_after = config["tracker"]["deleteOrphanedOrganizationsAfter"]

            self.loglevel = config["default"]["loglevel"]

        logging.basicConfig(level=self.loglevel,
                            format='%(asctime)s | %(levelname)s | %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')

        self.log = logging.getLogger(__name__)
        self.log.info("GitHub Backup Tool - Copyright (c) 2021 Thomann Bits & Beats GmbH - All Rights Reserved.")

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

    def get_warn_before_repository_deletion(self):
        duration = self.warn_before_repository_deletion
        return self.get_days(duration)

    def get_warn_before_orphaned_org_deletion(self):
        duration = self.warn_before_orphaned_org
        return self.get_days(duration)

    def get_delete_orphaned_org_after(self):
        duration = self.delete_orphaned_org_after
        return self.get_days(duration)

    def end(self):
        self.end_datetime = datetime.now()


class GithubApi:
    def __init__(self, config: Configuration):
        self.config = config

        self.github = Github(config.get_token())
        self.configured_organizations = config.get_configured_organizations()
        self.github_organizations = []

    def print_info(self):
        user = self.github.get_user()

        self.config.log.info("Accessing GitHub as %s", user.login)

        github_organizations = []

        for organization in self.get_github_organizations():
            github_organizations.append(organization.login)

        organizations_difference = [x for x in github_organizations if x not in self.configured_organizations]

        self.config.log.info("Organizations selected for backup:")
        for organization in self.configured_organizations:
            self.config.log.info("  %s", organization)

        if len(organizations_difference) > 0:
            self.config.log.info("Organizations not selected for backup:")

            for organization in organizations_difference:
                self.config.log.info("  %s", organization)

            self.config.log.info("There are organizations not selected for backup.")
            self.config.log.info("Potential data loss may occur.")

    def get_github_organizations(self):
        self.rate_limit_wait()
        self.github_organizations = self.github.get_user().get_orgs()
        return self.github_organizations

    def get_github_configured_organizations(self):
        self.rate_limit_wait()
        organizations = []

        for configured_organization in self.configured_organizations:
            for organization in self.github.get_user().get_orgs():
                if configured_organization == organization.login:
                    organizations.append(organization)

        return organizations

    def get_all_repositories_in_organization(self, organization):
        self.rate_limit_wait()
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

        requests_remaining = self.github.rate_limiting[0]
        request_limit = self.github.rate_limiting[1]

        if (request_limit - requests_remaining) <= 0:
            now = datetime.now(timezone.utc)
            minutes_to_wait = (reset_core - now)
            self.config.log.info("GitHub Rate Limit exceeded! Waiting for %s for reset.",
                                 naturaldelta(reset_core - now))

            wait_time = minutes_to_wait.total_seconds()
            time.sleep(wait_time)


class Git:
    def __init__(self, config: Configuration, github: GithubApi, ssh_key):
        self.backup_path = config.get_backup_path()
        self.config = config
        self.ssh_key = ssh_key
        self.github = github
        self.failed_repositories = []

        self.wrapper = "ssh_wrapper.sh"

        self.ssh_cmd = ""
        self.git_ssh_cmd = {}
        self.use_git_ssh_wrapper = False

        self.setup_ssh()

    def setup_ssh(self):
        ssh_wrapper = os.path.join(self.config.get_backup_path(), self.wrapper)

        if os.path.exists(ssh_wrapper):
            try:
                os.remove(ssh_wrapper)
            except OSError:
                pass

        ssh_str_path = str(self.config.get_ssh_key())

        if ssh_str_path != ".":
            # @todo Adjust best options depending on OpenSSH version.
            self.ssh_cmd = 'ssh -i {} -F /dev/null -o StrictHostKeyChecking=no'.format(ssh_str_path)
            self.git_ssh_cmd = {"GIT_SSH_COMMAND": self.ssh_cmd}

            git_version = cmd.Git().version_info
            self.use_git_ssh_wrapper = LooseVersion(
                "{}.{}".format(str(git_version[0]), str(git_version[1]))) < LooseVersion("2.3")

        if self.use_git_ssh_wrapper:
            with open(ssh_wrapper, "w") as file:
                file.write("#!/bin/bash\n")
                file.write(self.ssh_cmd + ' "$@"\n')
                file.close()

            os.chmod(ssh_wrapper, S_IXUSR | S_IRUSR | S_IWUSR)

            try:
                del self.git_ssh_cmd['GIT_SSH_COMMAND']
            except KeyError:
                pass

            self.git_ssh_cmd['GIT_SSH'] = os.path.abspath(ssh_wrapper)

    def get_failed_repositories(self):
        return self.failed_repositories

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
            with git.custom_environment(**self.git_ssh_cmd):
                response = git.ls_remote('-h', repository_url).split()

            if len(response) > 0:
                return True
            else:
                self.config.log.info("%s is empty", repository.full_name)
                return False
        except exc.GitCommandError as error:
            self.config.log.error("%s access restricted: %s" % (repository.full_name, error.status))
            self.config.error_count += 1
            return False

    def clone(self, repository: Repository):
        repository_path = self.get_repository_path(repository)
        repository_url = self.get_repository_url(repository)

        try:
            self.config.log.info("%s -> %s" % (repository.full_name, repository_path))
            Repo.clone_from(repository_url, repository_path, env=self.git_ssh_cmd)
            repo = Repo(repository_path)

            if self.use_git_ssh_wrapper:
                repo.git.config('merge.defaultToUpstream', 'true')

            tracker = Tracker(self.config)

            tracker.track_repository(repository.full_name, tracker.get_organization_id(repository.organization.login),
                                     datetime.now(),
                                     datetime.now(), True)

            self.update_local(repository)
        except exc.GitCommandError as error:
            self.config.log.error("%s cannot access repository: %s" % (repository.full_name, error.status))
            self.config.error_count += 1
            self.failed_repositories.append(repository.full_name)

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

        try:
            with repo.git.custom_environment(**self.git_ssh_cmd):
                repo.git.checkout('-b', branch_name)
                repo.git.checkout(default_branch)
        except exc.GitCommandError as error:
            self.config.log.error("%s %s checkout failed: %s" % (
                repository.full_name, branch_name, error.status))
            self.config.error_count += 1

        now = datetime.now()
        tracker = Tracker(self.config)
        tracker.track_branch(branch_name, tracker.get_repository_id(repository.full_name), now, now, True, True)

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

    def get_remote_branches(self, repository: Repo):
        remote_branches = []

        with repository.git.custom_environment(**self.git_ssh_cmd):
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

        # @todo Maybe also use the 'commits' table in repository_db for tracking.

        if commit_id not in self.get_remote_branches_commit_ids(repo):
            self.config.log.info("  %s -> %s" % (
                repository.full_name, str(default_branch) + "_abandoned_" + timestamp.strftime("%Y%m%d_%H%M%S")))
            self.checkout_abandoned_branch(repository, default_branch, timestamp)

            self.config.log.info("  %s %s <- origin/%s" % (
                repository.full_name, str(default_branch), str(default_branch)))
            self.reset_branch(repo, default_branch)

    def update_local(self, repository):
        repo = Repo(self.get_repository_path(repository))
        local_default_branch = str(repo.active_branch).strip()

        self.config.log.info("  %s <- remote", repository.full_name)

        try:
            with repo.git.custom_environment(**self.git_ssh_cmd):
                repo.remote().fetch()
        except exc.GitCommandError as error:
            self.config.log.error("%s fetch error: %s" % (repository.full_name, error.status))
            self.config.error_count += 1
            self.failed_repositories.append(repository.full_name)

        tracker = Tracker(self.config)

        tracker.track_repository(repository.full_name, tracker.get_organization_id(repository.organization.login),
                                 datetime.now(),
                                 datetime.now(), True)

        repo_id = tracker.get_repository_id(repository.full_name)

        for branch in self.get_remote_branches(repo):
            branch_name = str(branch).rsplit('/', 1)[-1].strip()

            try:
                if local_default_branch != branch_name:
                    with repo.git.custom_environment(**self.git_ssh_cmd):
                        repo.git.checkout('-b', branch_name, str(branch))
                    tracker.track_branch(branch_name, repo_id, datetime.now(), datetime.now(), False, True)
            except exc.GitCommandError as error:
                if error.status != 128:
                    self.config.log.error("%s %s -> %s failed: %s" % (
                        repository.full_name, str(branch), branch_name, error.status))
                    self.config.error_count += 1

            try:
                repo.git.merge('--ff-only')
                self.config.log.info("  %s %s <- origin/%s" % (repository.full_name, branch_name, branch_name))

                if local_default_branch != branch_name:
                    tracker.update_branch(branch_name, repo_id, datetime.now())
            except exc.GitCommandError as error:
                if error.status == 128:
                    self.config.log.error("%s %s <- origin/%s failed: %s" % (
                        repository.full_name, branch_name, branch_name, error.status))
                    self.config.error_count += 1
                    self.backup_branch(repository, branch_name)

        try:
            with repo.git.custom_environment(**self.git_ssh_cmd):
                repo.git.checkout(local_default_branch)
        except exc.GitCommandError as error:
            self.config.log.error("%s %s checkout failed: %s" % (
                repository.full_name, local_default_branch, error.status))
            self.failed_repositories.append(repository.full_name)

        tracker.update_repository(repository.full_name, tracker.get_organization_id(repository.organization.login),
                                  datetime.now())

    def remove_branch(self, branch_name, repository_name):
        repo = Repo(os.path.join(Path(self.config.get_backup_path()), Path(repository_name)))

        removed = False

        if "_abandoned_" in branch_name:
            try:
                repo.git.branch('-D', branch_name)
                removed = True
            except exc.GitCommandError as error:
                self.config.log.error("%s %s deletion failed: %s" % (repository_name, branch_name, error.status))
                self.config.error_count += 1
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
                    self.config.log.error("%s %s deletion failed: %s" % (repository_name, branch_name, error.status))
                    self.config.error_count += 1
                    removed = False

        return removed


class GithubBackup:
    def __init__(self, config: Configuration):
        self.config = config
        self.github_api = GithubApi(config)
        self.tracker = Tracker(self.config)

        self.github_api.print_info()

        self.start_datetime = datetime.now()

        if self.config.get_ssh_key() is None:
            self.config.log.warning("SSH Key not found.")

        self.git = Git(config, self.github_api, self.config.get_ssh_key())

    def backup_organizations(self):
        self.config.log.info("Backing up:")
        total_organizations = 0
        total_repositories = 0

        for organization in self.github_api.get_github_configured_organizations():
            total_organizations += 1
            repositories = 0

            tracker = Tracker(self.config)
            tracker.track_organization(organization.login, True)

            for repository in self.github_api.get_all_repositories_in_organization(organization):
                total_repositories += 1
                repositories += 1

                self.git.update(repository)

            self.config.log.info("Processed %s repositories in %s" % (repositories, organization.login))

        self.config.log.info(
            "Processed %s repositories in %s organizations." % (total_repositories, total_organizations))

    def log_failed_repositories(self):
        failed_repositories = self.git.get_failed_repositories()

        if len(failed_repositories) > 0:
            self.config.log.error("Could not backup:")
            for repository in failed_repositories:
                self.config.log.error("  %s", repository)
                self.config.error_count += 1

    def clean_branches_from_list(self, branches_list_tuples):
        for branch_tuple in branches_list_tuples:
            removed = self.git.remove_branch(branch_tuple[0], branch_tuple[1])
            if removed:
                self.config.log.info("  %s %s removed" % (branch_tuple[1], branch_tuple[0]))
                self.tracker.delete_branch(branch_tuple[0], branch_tuple[1])

    def clean_abandoned_branches(self):
        retention_period = self.config.get_delete_abandoned_branches_after()

        branches = self.tracker.get_abandoned_branches_older_than(retention_period)

        if len(branches) == 0:
            return

        self.config.log.info("Cleaning up abandoned branches:")
        self.clean_branches_from_list(branches)

    def clean_tracked_branches(self):
        retention_period = self.config.get_delete_removed_branches_after()

        branches = self.tracker.get_branches_older_than(retention_period)

        if len(branches) == 0:
            return

        self.config.log.info("Cleaning up branches:")
        self.clean_branches_from_list(branches)

    def clean_tracked_repositories(self):
        retention_period = self.config.get_delete_removed_repositories_after()

        repositories = self.tracker.get_repositories_older_than(retention_period, True)

        if len(repositories) == 0:
            return

        self.config.log.info("Cleaning up repositories:")

        for repository in repositories:
            shutil.rmtree(os.path.join(self.config.get_backup_path(), repository), ignore_errors=True)
            self.tracker.delete_repository(repository, repository.rsplit('/', 1)[0])
            self.config.log.info("  %s removed", repository)

    def clean_orphaned_organizations(self):
        organizations = self.tracker.get_untracked_organizations()

        for organization in organizations:
            local_organization_path = None

            try:
                local_organization_path = os.path.join(self.config.get_backup_path(), organization)
                local_repositories_in_organization = os.listdir(local_organization_path)
            except FileNotFoundError:
                local_repositories_in_organization = []

            if self.tracker.safe_to_delete_organization(organization) and len(local_repositories_in_organization) == 0:
                try:
                    shutil.rmtree(local_organization_path)
                except FileNotFoundError:
                    self.config.log.info("%s does not exist", local_organization_path)

                self.tracker.delete_organization(organization)
                self.config.log.info("%s removed", organization)
            else:
                self.config.log.warning("%s is orphaned and unhandled", organization)

    def warn_before_scheduled_repositories_deletion(self):
        remove_repos_after = self.config.get_delete_removed_repositories_after()
        warn_before_repo = self.config.get_warn_before_repository_deletion()
        deletion_repo_in = remove_repos_after - warn_before_repo

        repositories = self.tracker.get_repositories_older_than(deletion_repo_in, False)

        if deletion_repo_in < 0 or len(repositories) == 0:
            return

        self.config.log.warning("Repositories scheduled for deletion in %s days:", deletion_repo_in)

        for repository in repositories:
            self.config.log.warning("  %s", repository)
            self.tracker.do_not_warn_about_future_deletion(repository)

    def warn_before_scheduled_organizations_deletion(self):
        rem_orgs_after = self.config.get_delete_orphaned_org_after()
        warn_before_org = self.config.get_warn_before_orphaned_org_deletion()
        deletion_org_in = rem_orgs_after - warn_before_org

        organizations = self.tracker.get_untracked_organizations()

        if deletion_org_in <= 0 or len(organizations) == 0:
            return

        self.config.log.warning("Organizations scheduled for deletion in %s days:", deletion_org_in)

        for organization in organizations:
            self.config.log.warning("  %s", organization)
            self.tracker.do_not_warn_about_future_orphaned_org_deletion(organization)

    def cross_check_local_repositories(self):
        backup_dir = Path(os.path.abspath(self.config.get_backup_path()))

        organizations = []

        for file in os.listdir(backup_dir):
            if os.path.isdir(os.path.join(backup_dir, file)):
                organizations.append(file)

        github_organizations = []

        for organization in self.github_api.get_github_configured_organizations():
            github_organizations.append(organization.login)

        now = datetime.now()
        for organization in organizations:
            if not self.tracker.organization_exists(organization) and organization not in github_organizations:
                self.config.log.warning("Local orphaned organization found: %s", organization)
                self.tracker.track_organization(organization, False)
                organization_id = self.tracker.get_organization_id(organization)

                repositories = []

                for file in os.listdir(os.path.join(backup_dir, organization)):
                    if os.path.isdir(os.path.join(backup_dir, organization, file)):
                        repositories.append(file)

                for repository in repositories:
                    if not self.tracker.repository_exists(repository):
                        repository_name = organization + "/" + repository
                        self.tracker.track_repository(repository_name, organization_id, now, now, False)
                        self.config.log.warning("  Local orphaned repository found: %s", repository_name)

    def clean(self):
        self.clean_abandoned_branches()
        self.clean_tracked_branches()
        self.warn_before_scheduled_organizations_deletion()
        self.warn_before_scheduled_repositories_deletion()
        self.clean_tracked_repositories()
        self.clean_orphaned_organizations()
        self.log_failed_repositories()
        self.cross_check_local_repositories()

    def end(self):
        self.config.end()

        self.config.log.info("Backup ended. Duration: %s", naturaldelta(
            self.config.start_datetime - self.config.end_datetime))

        if self.config.error_count > 0:
            sys.exit(1)
