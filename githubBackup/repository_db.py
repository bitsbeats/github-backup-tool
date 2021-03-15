from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

Base = declarative_base()


class Organization(Base):
    __tablename__ = 'organizations'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    track = Column(Boolean(), nullable=False)


class Repository(Base):
    __tablename__ = 'repositories'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    created_date = Column(DateTime(), nullable=False)
    last_seen_in_github = Column(DateTime(), nullable=False)
    track = Column(Boolean(), nullable=False)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False)

    organization = relationship("Organization")


class Branch(Base):
    __tablename__ = 'branches'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    repository_id = Column(Integer, ForeignKey('repositories.id'))
    created_date = Column(DateTime(), nullable=False)
    last_seen_in_github = Column(DateTime(), nullable=False)
    abandoned = Column(Boolean(), nullable=False)
    track = Column(Boolean(), nullable=False)

    repository = relationship("Repository")


class Commit(Base):
    __tablename__ = 'commits'
    id = Column(Integer, primary_key=True)
    hash = Column(String(), nullable=False)
    saved_date = Column(DateTime(), nullable=False)

    repository_id = Column(Integer, ForeignKey('repositories.id'))
    branch_id = Column(Integer, ForeignKey('branches.id'), nullable=False)

    branch = relationship("Branch")


class Tracker:
    def __init__(self, config):
        self.config = config

        if self.config.get_track_abandoned_branches():
            self.db = self.config.get_track_db()
            self.engine = create_engine('sqlite:///' + self.db)
            Base.metadata.create_all(self.engine)
            Base.metadata.bind = self.engine
            self.db_session = sessionmaker(bind=self.engine)
            self.session = self.db_session()

    def track_organization(self, organization, track):
        records = self.session.query(Organization).filter(Organization.name == organization).all()

        if len(records) >= 1:
            return
        else:
            self.session.add(Organization(name=organization, track=track))
            self.session.commit()

    def get_organization_id(self, organization):
        record = self.session.query(Organization).filter(Organization.name == organization).first()

        return record.id

    def get_organizations(self):
        records = self.session.query(Organization).all()

        return records

    # TODO: remove cascade whole organization and its deps
    def delete_organization(self, organization):
        records = self.session.query(Organization).filter(Organization.name == organization).all()
        return

    def track_repository(self, repository, organization_id, create_date, last_seen_in_github, track):
        records = self.session.query(Repository).join(Organization,
                                                      Organization.id == Repository.organization_id).filter(
            Repository.name == repository).all()

        if len(records) >= 1:
            for record in records:
                record.last_seen_in_github = datetime.now()
        else:
            self.session.add(
                Repository(name=repository, created_date=create_date,
                           last_seen_in_github=create_date,
                           track=track, organization_id=organization_id))

        self.session.commit()

    def get_repository_id(self, repository):
        record = self.session.query(Repository).filter(Repository.name == repository).first()
        return record.id

    def get_tracked_repositories(self):
        records = self.session.query(Repository).join(Organization,
                                                      Organization.id == Repository.organization_id).filter(
            Repository.track == True).all()

        list_of_tracked_repositories = []

        for record in records:
            list_of_tracked_repositories.append(record.name)

        return list_of_tracked_repositories

    def update_repository(self, repository, organization, date):
        records = self.session.query(Repository).join(Organization,
                                                      Organization.id == Repository.organization_id).filter(
            Repository.name == repository).all()

        if (len(records)) >= 1:
            for record in records:
                record.last_seen_in_github = datetime.now()

            self.session.commit()

    def delete_repository(self, repository, organization):
        records = self.session.query(Repository).join(Organization,
                                                      Organization.id == Repository.organization_id).filter(
            Repository.name == repository).all()

        if (len(records)) >= 1:
            self.delete_all_branches(repository)

            for record in records:
                self.session.delete(record)

            self.session.commit()

    def get_repositories_older_than(self, time_period):
        ago = datetime.now() - timedelta(days=time_period)

        records = self.session.query(Repository).join(Organization,
                                                      Organization.id == Repository.organization_id).filter(
            Repository.last_seen_in_github <= ago).all()

        list_of_repositories = []
        for record in records:
            list_of_repositories.append(record.name)

        return list_of_repositories

    def track_branch(self, branch_name, repository_id, create_date, last_seen_in_github, abandoned, track):
        records = self.session.query(Branch).join(Repository, Repository.id == Branch.repository_id).filter(
            Branch.name == branch_name).all()

        if (len(records)) >= 1:
            for record in records:
                if not record.abandoned:
                    record.last_seen_in_github = datetime.now()
        else:
            self.session.add(
                Branch(name=branch_name, repository_id=repository_id, created_date=create_date,
                       last_seen_in_github=create_date, abandoned=abandoned,
                       track=track))

        self.session.commit()

    def update_branch(self, branch_name, repository_id, date):
        records = self.session.query(Branch).join(Repository, Repository.id == Branch.repository_id).filter(
            Branch.name == branch_name).all()

        if (len(records)) >= 1:
            for record in records:
                record.last_seen_in_github = datetime.now()
                self.session.commit()
        else:
            date = datetime.now()
            self.track_branch(branch_name, repository_id, date, date, False, True)

    def get_branches_older_than(self, time_period):
        ago = datetime.now() - timedelta(days=time_period)

        records = self.session.query(Branch).join(Repository, Repository.id == Branch.repository_id).filter(
            Branch.last_seen_in_github <= ago, Branch.abandoned == False).all()

        list_of_branches = []
        for record in records:
            list_of_branches.append(tuple((record.name, record.repository.name)))

        return list_of_branches

    def get_abandoned_branches_older_than(self, time_period):
        ago = datetime.now() - timedelta(days=time_period)

        records = self.session.query(Branch).join(Repository, Repository.id == Branch.repository_id).filter(
            Branch.created_date <= ago, Branch.abandoned == True).all()

        list_of_branches = []
        for record in records:
            list_of_branches.append(tuple((record.name, record.repository.name)))

        return list_of_branches

    def delete_branch(self, branch_name, repository):
        records = self.session.query(Branch).join(Repository, Repository.id == Branch.repository_id).filter(
            Branch.name == branch_name).all()

        if (len(records)) >= 1:
            for record in records:
                self.session.delete(record)

            self.session.commit()

    def delete_all_branches(self, repository):
        records = self.session.query(Branch).join(Repository, Repository.id == Branch.repository_id).filter(
            Repository.name == repository).all()

        if (len(records)) >= 1:
            for record in records:
                self.session.delete(record)

            self.session.commit()
