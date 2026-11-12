"""Baseline schema: sessions, event log, results, samples, runs, reports.

Equivalent to ``Base.metadata.create_all`` at the time of writing – the tests
assert the two stay identical (``tests/test_migrations.py``).

The gameplay document itself lives in ``game_sessions.state`` (JSON); the other
tables are the queryable read models (leaderboard, science database, reports).

Revision ID: 0001
Revises: none
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('game_sessions',
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('player_name', sa.String(length=80), nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=False),
    sa.Column('environment', sa.String(length=32), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('state', sa.JSON(), nullable=False),
    sa.Column('readiness', sa.Float(), nullable=False),
    sa.Column('missions_completed', sa.Integer(), nullable=False),
    sa.Column('finished', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('game_id', name=op.f('pk_game_sessions'))
    )
    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_game_sessions_player_name'), ['player_name'], unique=False)

    op.create_table('idempotency_keys',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('response', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_idempotency_keys'))
    )
    with op.batch_alter_table('idempotency_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_idempotency_keys_game_id'), ['game_id'], unique=False)

    op.create_table('earth_reports',
    sa.Column('report_id', sa.String(length=64), nullable=False),
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('region_id', sa.String(length=64), nullable=False),
    sa.Column('layer_id', sa.String(length=64), nullable=False),
    sa.Column('period_before', sa.String(length=32), nullable=False),
    sa.Column('period_after', sa.String(length=32), nullable=False),
    sa.Column('provenance', sa.String(length=16), nullable=False),
    sa.Column('verdict', sa.String(length=16), nullable=False),
    sa.Column('mean_delta', sa.Float(), nullable=False),
    sa.Column('analysis', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['game_sessions.game_id'], name=op.f('fk_earth_reports_game_id_game_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('report_id', name=op.f('pk_earth_reports'))
    )
    with op.batch_alter_table('earth_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_earth_reports_game_id'), ['game_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_earth_reports_layer_id'), ['layer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_earth_reports_region_id'), ['region_id'], unique=False)

    op.create_table('event_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('mission_id', sa.String(length=64), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['game_sessions.game_id'], name=op.f('fk_event_log_game_id_game_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_event_log')),
    sa.UniqueConstraint('game_id', 'seq', name='uq_event_game_seq')
    )
    with op.batch_alter_table('event_log', schema=None) as batch_op:
        batch_op.create_index('ix_event_game_seq', ['game_id', 'seq'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_log_game_id'), ['game_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_log_type'), ['type'], unique=False)

    op.create_table('mission_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('mission_id', sa.String(length=64), nullable=False),
    sa.Column('stage', sa.Integer(), nullable=False),
    sa.Column('score_pct', sa.Float(), nullable=False),
    sa.Column('grade', sa.String(length=4), nullable=False),
    sa.Column('mistakes', sa.Integer(), nullable=False),
    sa.Column('elapsed_seconds', sa.Float(), nullable=False),
    sa.Column('report', sa.JSON(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['game_sessions.game_id'], name=op.f('fk_mission_results_game_id_game_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mission_results')),
    sa.UniqueConstraint('game_id', 'mission_id', name='uq_result_game_mission')
    )
    with op.batch_alter_table('mission_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mission_results_game_id'), ['game_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mission_results_mission_id'), ['mission_id'], unique=False)

    op.create_table('science_samples',
    sa.Column('sample_id', sa.String(length=64), nullable=False),
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('mission_id', sa.String(length=64), nullable=False),
    sa.Column('site', sa.String(length=80), nullable=False),
    sa.Column('rock_type', sa.String(length=80), nullable=False),
    sa.Column('composition', sa.JSON(), nullable=False),
    sa.Column('analysis', sa.JSON(), nullable=False),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('analysed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['game_sessions.game_id'], name=op.f('fk_science_samples_game_id_game_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sample_id', name=op.f('pk_science_samples'))
    )
    with op.batch_alter_table('science_samples', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_science_samples_game_id'), ['game_id'], unique=False)

    op.create_table('simulation_runs',
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('game_id', sa.String(length=64), nullable=False),
    sa.Column('mission_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('score_pct', sa.Float(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['game_id'], ['game_sessions.game_id'], name=op.f('fk_simulation_runs_game_id_game_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', name=op.f('pk_simulation_runs'))
    )
    with op.batch_alter_table('simulation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_simulation_runs_game_id'), ['game_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_simulation_runs_mission_id'), ['mission_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('simulation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_simulation_runs_mission_id'))
        batch_op.drop_index(batch_op.f('ix_simulation_runs_game_id'))

    op.drop_table('simulation_runs')
    with op.batch_alter_table('science_samples', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_science_samples_game_id'))

    op.drop_table('science_samples')
    with op.batch_alter_table('mission_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mission_results_mission_id'))
        batch_op.drop_index(batch_op.f('ix_mission_results_game_id'))

    op.drop_table('mission_results')
    with op.batch_alter_table('event_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_log_type'))
        batch_op.drop_index(batch_op.f('ix_event_log_game_id'))
        batch_op.drop_index('ix_event_game_seq')

    op.drop_table('event_log')
    with op.batch_alter_table('earth_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_earth_reports_region_id'))
        batch_op.drop_index(batch_op.f('ix_earth_reports_layer_id'))
        batch_op.drop_index(batch_op.f('ix_earth_reports_game_id'))

    op.drop_table('earth_reports')
    with op.batch_alter_table('idempotency_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_idempotency_keys_game_id'))

    op.drop_table('idempotency_keys')
    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_game_sessions_player_name'))

    op.drop_table('game_sessions')
