create table calls (
    id bigint generated always as identity primary key,
    ts timestamptz not null,
    route text,
    func text,
    module text,
    provider text,
    model text,
    canonical_model text,
    endpoint text,
    input_tokens bigint,
    output_tokens bigint,
    cache_read_tokens bigint,
    cache_write_tokens bigint,
    reasoning_tokens bigint,
    cost_usd numeric(16, 8),
    price_id text,
    cost_status text check (
        cost_status is null or cost_status in ('priced', 'partial', 'unpriced')
    ),
    latency_ms integer,
    ttft_ms integer,
    status text,
    status_code text check (
        status_code is null or status_code in ('unset', 'ok', 'error')
    ),
    finish_reason text,
    finish_reason_raw text,
    error boolean,
    error_type text,
    stream boolean,
    batch boolean,
    session_id text,
    template_hash text,
    unit_name text,
    unit_count numeric(14, 4),
    tool_names jsonb,
    tags jsonb,
    environment text,
    sdk text,
    sdk_version text,
    request_id text
);

create index calls_ts_idx on calls (ts);
create index calls_func_ts_idx on calls (func, ts);
create index calls_route_ts_idx on calls (route, ts);
create index calls_model_ts_idx on calls (provider, model, ts);
