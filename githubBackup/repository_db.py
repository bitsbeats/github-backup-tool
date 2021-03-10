from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, Integer, String, DateTime
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


class Branch(Base):
    __tablename__ = 'branches'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    repository = Column(String(), nullable=False)
    created_date = Column(DateTime(), nullable=False)
    last_seen_in_github = Column(DateTime(), nullable=False)
    abandoned = Column(Boolean(), nullable=False)
    track = Column(Boolean(), nullable=False)


class Commit(Base):
    __tablename__ = 'commits'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    saved_date = Column(DateTime(), nullable=False)


class Repository(Base):
    __tablename__ = 'repositories'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    organization = Column(String(), nullable=False)
    created_date = Column(DateTime(), nullable=False)
    last_seen_in_github = Column(DateTime(), nullable=False)
    track = Column(Boolean(), nullable=False)


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

    def track_repository(self, repository, organization, create_date, last_seen_in_github, track):
        records = self.session.query(Repository).filter(Repository.name == repository,
                                                        Repository.organization == organization).all()

        if (len(records)) >= 1:
            for record in records:
                record.last_seen_in_github = datetime.now()
        else:
            self.session.add(
                Repository(name=repository, organization=organization, created_date=create_date,
                           last_seen_in_github=create_date,
                           track=track))

        self.session.commit()

    def get_tracked_repositories(self):
        records = self.session.query(Repository).all()

        list_of_tracked_repositories = []

        for record in records:
            list_of_tracked_repositories.append(record.name)

        return list_of_tracked_repositories

    def update_repository(self, repository, organization, date):
        records = self.session.query(Repository).filter(Repository.name == repository,
                                                        Repository.organization == organization).all()
        if (len(records)) >= 1:
            for record in records:
                record.last_seen_in_github = datetime.now()

            self.session.commit()

    def delete_repository(self, repository, organization):
        records = self.session.query(Repository).filter(Repository.name == repository,
                                                        Repository.organization == organization).all()

        if (len(records)) >= 1:
            for record in records:
                self.session.delete(record)

            self.session.commit()

            self.delete_all_branches(repository)

    def get_repositories_older_than(self, time_period):
        ago = datetime.now() - timedelta(days=time_period)

        records = self.session.query(Repository).filter(Repository.last_seen_in_github <= ago).all()

        list_of_repositories = []
        for record in records:
            list_of_repositories.append(record.name)

        return list_of_repositories

    def track_branch(self, branch_name, repository, create_date, last_seen_in_github, abandoned, track):
        records = self.session.query(Branch).filter(Branch.name == branch_name, Branch.repository == repository,
                                                    Branch.abandoned.is_(abandoned)).all()

        if (len(records)) >= 1:
            for record in records:
                if not record.abandoned:
                    record.last_seen_in_github = datetime.now()
        else:
            self.session.add(
                Branch(name=branch_name, repository=repository, created_date=create_date,
                       last_seen_in_github=create_date, abandoned=abandoned,
                       track=track))

        self.session.commit()

    def update_branch(self, branch_name, repository, date):
        records = self.session.query(Branch).filter(Branch.name == branch_name,
                                                    Branch.repository == repository).all()
        if (len(records)) >= 1:
            for record in records:
                record.last_seen_in_github = datetime.now()
                self.session.commit()
        else:
            date = datetime.now()
            self.track_branch(branch_name, repository, date, date, False, True)

    def get_branches_older_than(self, time_period):
        ago = datetime.now() - timedelta(days=time_period)

        records = self.session.query(Branch).filter(Branch.last_seen_in_github <= ago, Branch.abandoned == False).all()

        list_of_branches = []
        for record in records:
            list_of_branches.append(tuple((record.name, record.repository)))

        return list_of_branches

    def get_abandoned_branches_older_than(self, time_period):
        ago = datetime.now() - timedelta(days=time_period)

        records = self.session.query(Branch).filter(Branch.created_date <= ago, Branch.abandoned == True).all()

        list_of_branches = []
        for record in records:
            list_of_branches.append(tuple((record.name, record.repository)))

        return list_of_branches

    def delete_branch(self, branch_name, repository):
        records = self.session.query(Branch).filter(Branch.name == branch_name,
                                                    Branch.repository == repository).all()

        if (len(records)) >= 1:
            for record in records:
                self.session.delete(record)

            self.session.commit()

    def delete_all_branches(self, repository):
        records = self.session.query(Branch).filter(Branch.repository == repository).all()

        if (len(records)) >= 1:
            for record in records:
                self.session.delete(record)

            self.session.commit()
