#!/usr/bin/env python3

import argparse


from github_backup.backup import GithubBackup, Configuration


def parse_args():
    parser = argparse.ArgumentParser(description='Backup GitHub Repositories')
    parser.add_argument('-c',
                        '--config',
                        dest='config',
                        help='configuration file to use')
    return parser.parse_args()


def main():
    args = parse_args()

    if args.config:
        config = Configuration(args.config)
    else:
        config = Configuration("config.yaml")

    backup = GithubBackup(config)
    backup.backup_organizations()
    backup.clean()
    backup.end()


if __name__ == '__main__':
    main()
