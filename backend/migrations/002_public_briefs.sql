-- 免登录复验简报：会签发布时在同一事务内冻结一份不含观众记录的聚合 payload。
-- run_id 唯一保证发布重试不会重复签发；token 为不透明访问凭证。
CREATE TABLE public_briefs (
    token text PRIMARY KEY,
    run_id bigint NOT NULL UNIQUE REFERENCES analysis_runs(id),
    study_id bigint NOT NULL REFERENCES studies(id),
    revision int NOT NULL,
    held_orders text[] NOT NULL DEFAULT '{}',
    payload jsonb NOT NULL,
    created_by text NOT NULL REFERENCES accounts(username),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX public_briefs_study ON public_briefs(study_id, created_at);
