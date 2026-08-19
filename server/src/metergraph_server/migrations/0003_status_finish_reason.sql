alter table calls add column if not exists status_code text;
alter table calls add column if not exists finish_reason text;
alter table calls add column if not exists finish_reason_raw text;

alter table calls drop constraint if exists calls_status_code_check;
alter table calls add constraint calls_status_code_check check (
    status_code is null or status_code in ('unset', 'ok', 'error')
);

update calls
set status_code = case
    when error is true or lower(status) in ('error', 'failed') then 'error'
    else 'unset'
end
where status_code is null;

update calls
set finish_reason = case replace(lower(status), '_', '-')
    when 'stop' then 'stop'
    when 'end-turn' then 'stop'
    when 'stop-sequence' then 'stop'
    when 'completed' then 'stop'
    when 'succeeded' then 'stop'
    when 'length' then 'length'
    when 'max-tokens' then 'length'
    when 'max-output-tokens' then 'length'
    when 'content-filter' then 'content-filter'
    when 'safety' then 'content-filter'
    when 'blocked' then 'content-filter'
    when 'tool-calls' then 'tool-calls'
    when 'tool-use' then 'tool-calls'
    when 'function-call' then 'tool-calls'
    when 'other' then 'other'
    when 'unknown' then 'other'
end
where finish_reason is null;
