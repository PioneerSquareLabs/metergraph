import assert from 'node:assert/strict'
import test from 'node:test'

import { buildSearchParams } from '../src/api.js'
import { buildEnvironmentQuery, environmentKey } from '../src/environment-selection.js'
import { mockApi } from '../src/mock.js'
import {
  callFinishReason,
  callFinishReasonLabel,
  callHealth,
  callHealthLabel,
} from '../src/call-status.js'

test('serializes every selected environment and an explicit untagged choice', () => {
  const params = buildSearchParams({
    environment: ['demo', 'prod'],
    include_untagged: false,
    empty: '',
  })

  assert.deepEqual(params.getAll('environment'), ['demo', 'prod'])
  assert.equal(params.get('include_untagged'), 'false')
  assert.equal(params.has('empty'), false)
})

test('keeps an explicit empty environment selection when options change', () => {
  assert.deepEqual(
    buildEnvironmentQuery([{ value: 'demo', calls: 4 }], new Set()),
    { environment: [], includeUntagged: false },
  )
})

test('selects named and untagged environments independently', () => {
  const options = [{ value: 'prod', calls: 4 }, { value: null, calls: 2 }]
  const selected = new Set([environmentKey('prod'), environmentKey(null)])

  assert.deepEqual(buildEnvironmentQuery(options, selected), {
    environment: ['prod'],
    includeUntagged: true,
  })
  assert.notEqual(environmentKey(null), environmentKey('__metergraph_untagged__'))
})

test('mock mode returns no traffic when no environments are selected', async () => {
  const params = { environment: [], include_untagged: false }

  assert.deepEqual(await mockApi('/v1/usage', params), { items: [] })
  assert.deepEqual(await mockApi('/v1/calls', params), { items: [] })
  assert.deepEqual((await mockApi('/v1/usage/timeseries', params)).series, [])
})

test('model finish reasons are never presented as operational health', () => {
  assert.equal(callHealth({ status_code: 'unset', finish_reason: 'stop' }), 'unset')
  assert.equal(callHealth({ status: 'tool-calls' }), 'unset')
  assert.equal(callFinishReason({ status: 'tool-calls' }), 'tool-calls')
  assert.equal(callHealth({ status_code: 'error', error_type: 'timeout' }), 'error')
})

test('presents telemetry status values as plain-language dashboard labels', () => {
  assert.equal(callFinishReasonLabel({ finish_reason: 'stop' }), 'Completed')
  assert.equal(callFinishReasonLabel({ finish_reason: 'tool-calls' }), 'Tool requested')
  assert.equal(callFinishReasonLabel({ finish_reason: 'length' }), 'Token limit')
  assert.equal(callFinishReasonLabel({ finish_reason: 'content-filter' }), 'Content filtered')
  assert.equal(callHealthLabel({ status_code: 'unset' }), 'No error')
  assert.equal(callHealthLabel({ status_code: 'ok' }), 'OK')
  assert.equal(callHealthLabel({ status_code: 'error', error_type: 'DemoTimeout' }), 'Error')
})

test('mock mode exposes the status and finish-reason customer scenario', async () => {
  const { items } = await mockApi('/v1/calls', {
    func: 'demo.agent:status_lifecycle',
    environment: ['demo'],
    include_untagged: false,
  })

  assert.deepEqual(
    items.map((call) => [callHealth(call), callFinishReason(call), call.error_type]),
    [
      ['unset', 'tool-calls', null],
      ['unset', 'stop', null],
      ['error', null, 'timeout'],
    ],
  )
})
