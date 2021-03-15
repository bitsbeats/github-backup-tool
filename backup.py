#!/usr/bin/env python3

import argparse
from datetime import datetime

from humanize import naturaldelta

from githubBackup.github_backup import GithubBackup, Configuration


def parse_args():
    parser = argparse.ArgumentParser(description='Backup GitHub Repositories')
    parser.add_argument('-c',
                        '--config',
                        dest='config',
                        help='configuration file to use')
    return parser.parse_args()


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
    backup.clean_tracked_branches()
    backup.clean_tracked_repositories()
    backup.print_failed_repositories()

    end = datetime.now()
    print("Backup ended:", end.strftime("%d.%m.%Y %H:%M:%S"), "Duration:", naturaldelta(now - end))


if __name__ == '__main__':
    main()
