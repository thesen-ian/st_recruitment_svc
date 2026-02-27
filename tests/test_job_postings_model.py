from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
import pytest

from st_recruitment_svc.models.base import (
    JobPosting,
    JobPostingQuestion,
    JobPostingQuestionOption,
    JobPostingState,
    QuestionType,
    User,
    UserRole,
)


def test_job_posting_questions_options_cascade(db_session):
    # create a company user
    company = User(email='comp@example.com', password_hash='h', role=UserRole.company)
    db_session.add(company)
    db_session.flush()

    # create a job posting with questions and mcq options
    jp = JobPosting(company_id=company.id, title='Role', description='Desc')
    q1 = JobPostingQuestion(type=QuestionType.text, prompt='Tell us about yourself', is_required=True, position=0)
    q2 = JobPostingQuestion(type=QuestionType.mcq, prompt='Choose one', is_required=False, position=1)
    opt1 = JobPostingQuestionOption(label='Option A', position=0)
    opt2 = JobPostingQuestionOption(label='Option B', position=1)

    q2.options.extend([opt1, opt2])
    jp.questions.extend([q1, q2])

    db_session.add(jp)
    db_session.commit()

    # Ensure persisted using select() API
    assert db_session.execute(select(JobPosting).filter_by(id=jp.id)).scalar_one()
    questions = db_session.execute(select(JobPostingQuestion).filter_by(job_posting_id=jp.id)).scalars().all()
    assert len(questions) == 2
    # options for q2
    options = db_session.execute(select(JobPostingQuestionOption).filter_by(question_id=q2.id)).scalars().all()
    assert len(options) == 2

    # deleting job posting cascades to questions and options
    db_session.delete(jp)
    db_session.commit()

    assert len(db_session.execute(select(JobPosting).filter_by(id=jp.id)).scalars().all()) == 0
    assert len(db_session.execute(select(JobPostingQuestion).filter_by(job_posting_id=jp.id)).scalars().all()) == 0
    assert len(db_session.execute(select(JobPostingQuestionOption).filter_by(question_id=q2.id)).scalars().all()) == 0


def test_job_posting_state_constraint(db_session):
    # create a company user
    company = User(email='comp2@example.com', password_hash='h', role=UserRole.company)
    db_session.add(company)
    db_session.flush()

    # Attempt to use an invalid state should fail on flush/commit
    jp = JobPosting(company_id=company.id, title='Role 2', description='Desc', state='invalid_state')
    db_session.add(jp)
    with pytest.raises((IntegrityError, ValueError)):
        db_session.flush()
