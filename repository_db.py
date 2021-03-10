from sqlalchemy import Boolean, Column, Integer, String, Time
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import sqlite3

from github_backup import Configuration

Base = declarative_base()


class Branch(Base):
    __tablename__ = 'branches'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    created_date = Column(Time(), nullable=False)
    track = Column(Boolean(), nullable=False)


class Commit(Base):
    __tablename__ = 'commits'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    saved_date = Column(Time(), nullable=False)


class Repository(Base):
    __tablename__ = 'repositories'
    id = Column(Integer, primary_key=True)
    name = Column(String(), nullable=False)
    created_date = Column(Time(), nullable=False)
    track = Column(Boolean(), nullable=False)


class Tracker():
    def __init__(self, config: Configuration):
        self.config = config

        if self.config.track_abandoned_branches:
            self.db = self.config.abandoned_branches_db
            self.engine = create_engine('sqlite:///' + self.db)
            Base.metadata.create_all(self.engine)
            Base.metadata.bind = self.engine
            DBSession = sessionmaker(bind=self.engine)
            self.session = DBSession()

    def create_repo(self, repository, date):
        self.session.add(Repository(name=repository, date=date))
