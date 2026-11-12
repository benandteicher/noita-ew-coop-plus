"""Production Lua terrain boundary under controlled engine outcomes.

The fake apply callback records actual fake-cell state, including air removal.
These tests establish adapter ordering/failure behavior, not native pixel proof.
"""

from pathlib import Path
import unittest

from lupa import LuaRuntime


MODULE = Path(__file__).resolve().parents[1] / "quant.ew/files/system/world_sync/terrain_feedback.lua"


def fixture():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().module_path = MODULE.as_posix()
    lua.execute(
        r"""
        local factory = dofile(module_path)
        sent, events, reports = {}, {}, {}
        cells = { [0] = 6, [1] = 6, [2] = 6 }
        world_num, frame, apply_calls, loaded = 0, 100, 0, true
        GameGetFrameNum = function() return frame end
        GameGetRealWorldTimeSinceStarted = function() return 100.25 end
        local function event(value) events[#events + 1] = value end
        function le(value, count)
            local out = {}
            for i = 1, count do
                out[i] = string.char(value % 256)
                value = math.floor(value / 256)
            end
            return table.concat(out)
        end
        local function unresolved_one(material)
            local header = le(0, 4) .. le(0, 4) .. string.char(127, 127) .. le(3, 2)
            local function run(length, value)
                return le(length - 1, 2) .. le(value, 2) .. string.char(0)
            end
            return header .. run(1, 65535) .. run(1, material) .. run(16382, 65535)
        end
        remaining_zero, remaining_six = unresolved_one(0), unresolved_one(6)
        function apply_fake(data)
            apply_calls = apply_calls + 1
            event('apply:' .. data)
            if data == 'empty' then return true, nil, loaded end -- no required cells, even if unloaded
            if not loaded then return false, data, false end -- no verified writes
            if data == 'throw' then error('injected engine write failure') end
            if data == 'partial_throw' then
                cells[0] = 0
                error('injected failure after actual removal')
            end
            if data == 'bad_remaining' then return false, nil, true end
            if data == 'missing_loaded' then return false, remaining_zero end -- deliberately old API
            if data == 'partial_air' then
                cells[0] = 0 -- only this successful cell becomes Unknown
                return false, remaining_zero, true
            elseif data == 'partial_six' then
                cells[0] = 6
                return false, remaining_six, true
            elseif data == remaining_zero or data == remaining_six then
                if not resolve_partial then return false, data, true end
                cells[1] = data == remaining_zero and 0 or 6
                event('applied:remaining')
                return true, nil, true
            end
            if data == 'air' then cells[0], cells[1] = 0, 0
            elseif data == 'six' then cells[0], cells[1] = 6, 6
            elseif data == 'sparse_air' then cells[0] = 0
            elseif data == 'sparse_new' then cells[2] = 3
            else error('unexpected fake terrain blob') end
            event('applied:' .. data)
            return true, nil, true
        end
        boundary = factory.new {
            apply = apply_fake,
            send = function(key, data)
                if key == 2 and fail_ack then error('injected ACK transport failure') end
                if key == 5 and fail_progress then error('injected progress transport failure') end
                if key == 6 and fail_pause then error('injected pause transport failure') end
                sent[#sent + 1] = { key = key, data = data }
                event('send:' .. key)
            end,
            world = function() return world_num end,
            report = function(err) reports[#reports + 1] = tostring(err) end,
        }
        function count_sent(key)
            local n = 0
            for _, item in ipairs(sent) do if item.key == key then n = n + 1 end end
            return n
        end
        function last_sent(key)
            for i = #sent, 1, -1 do if sent[i].key == key then return sent[i].data end end
        end
        boundary:begin()
        hello = assert(last_sent(3), 'begin must send the capability/session HELLO')
        assert(#hello == 9 and hello:byte(1) == 2, 'wrong terrain v2 HELLO format')
        session = hello:sub(2, 5)
        function command(kind, token, data, cx, cy, world, command_session)
            return string.char(kind) .. le(token, 8) .. le(world or world_num, 4)
                .. (command_session or session) .. le(cx or 0, 4) .. le(cy or 0, 4) .. (data or '')
        end
        function reset(token)
            boundary:handle(command(2, token or 1))
        end
        function pause(token, parent)
            boundary:handle(command(3, token, le(parent, 8)))
        end
        function applied_ack(token)
            local data = assert(last_sent(2), 'missing application ACK')
            assert(data == le(token, 8) .. session, 'ACK must carry exact opaque token/session')
        end
        """
    )
    return lua


class TerrainFeedbackBarrierTests(unittest.TestCase):
    def test_startup_requires_matching_reset_before_any_capture(self):
        fixture().execute(
            """
            assert(not boundary:before_capture(0, 0))
            assert(count_sent(2) == 0 and apply_calls == 0)
            reset(1)
            applied_ack(1)
            assert(boundary:before_capture(0, 0))
            assert(apply_calls == 0, 'reset must not rebuild terrain')
            """
        )

    def test_air_removal_finishes_before_ack_then_identical_new_edit_is_sampleable(self):
        fixture().execute(
            """
            reset(1)
            event_before = #events
            local old_sample = le(cells[0], 2) .. le(cells[1], 2)
            boundary:handle(command(0, 2, 'air'))
            assert(cells[0] == 0 and cells[1] == 0, 'actual fake cells must be air')
            applied_ack(2)
            assert(events[event_before + 1] == 'apply:air')
            assert(events[event_before + 2] == 'applied:air')
            assert(events[event_before + 3] == 'send:2', 'ACK preceded application completion')
            cells[0], cells[1] = 6, 6 -- legitimate simulation after application
            assert(boundary:before_capture(0, 0))
            local new_sample = le(cells[0], 2) .. le(cells[1], 2)
            assert(new_sample == old_sample, 'freshness must not depend on material bytes')
            assert(apply_calls == 1)
            assert(count_sent(5) == 0, 'complete application must not invent partial progress')
            """
        )

    def test_unloaded_application_defers_without_ack_and_only_its_chunk_is_blocked(self):
        fixture().execute(
            """
            reset(1)
            local ack_count = count_sent(2)
            loaded = false
            boundary:handle(command(0, 2, 'air'))
            assert(cells[0] == 6 and cells[1] == 6, 'preflight failure wrote cells')
            assert(count_sent(2) == ack_count)
            local progress_count = count_sent(5)
            assert(not boundary:before_capture(0, 0), 'unloaded native result allowed a terrain sample')
            assert(count_sent(4) == 0 and count_sent(5) == progress_count, 'unchanged unloaded state faulted or spammed progress')
            assert(boundary:before_capture(1, 0), 'one deferred chunk blocked unrelated capture')
            loaded = true
            assert(boundary:before_capture(0, 0), 'loaded chunk did not apply before capture')
            assert(cells[0] == 0 and cells[1] == 0)
            applied_ack(2)
            local calls = apply_calls
            assert(boundary:before_capture(0, 0))
            assert(apply_calls == calls, 'completed write was replayed on ordinary capture')
            """
        )

    def test_retire_drops_deferred_work_before_ack_and_revisit_accepts_new_generation(self):
        fixture().execute(
            """
            reset(1)
            loaded = false
            boundary:handle(command(0, 2, 'air'))
            boundary:handle(command(1, 3))
            applied_ack(3)
            local calls = apply_calls
            loaded = true
            assert(boundary:before_capture(0, 0))
            assert(apply_calls == calls and cells[0] == 6, 'retired pending work was replayed')
            boundary:handle(command(0, 4, 'air'))
            applied_ack(4)
            assert(boundary:before_capture(0, 0) and cells[0] == 0)
            """
        )

    def test_all_unknown_noop_completes_without_retaining_an_unloaded_flight(self):
        fixture().execute(
            """
            reset(1)
            loaded = false
            boundary:handle(command(0, 2, 'empty'))
            applied_ack(2)
            assert(count_sent(4) == 0 and count_sent(5) == 0)
            local calls = apply_calls
            loaded = true
            assert(boundary:before_capture(0, 0))
            assert(apply_calls == calls, 'empty obligation was retained and retried')
            assert(cells[0] == 6 and cells[1] == 6)
            """
        )

    def test_duplicate_completed_command_does_not_overwrite_legitimate_edit(self):
        fixture().execute(
            """
            reset(1)
            local update = command(0, 2, 'air')
            boundary:handle(update)
            applied_ack(2)
            cells[0], cells[1] = 6, 6
            local calls = apply_calls
            boundary:handle(update)
            assert(apply_calls == calls, 'duplicate command re-applied completed terrain')
            assert(cells[0] == 6 and cells[1] == 6)
            assert(boundary:before_capture(0, 0))
            """
        )

    def test_new_generation_replaces_pending_composed_targets_without_touching_completed_cells(self):
        fixture().execute(
            """
            reset(1)
            boundary:handle(command(0, 2, 'partial_six'))
            assert(last_sent(5) == le(2, 8) .. session .. remaining_six)
            cells[0] = 7 -- legitimate edit to the completed cell
            local ack_count = count_sent(2)
            pause(3, 2)
            assert(last_sent(6) == le(3, 8) .. session .. remaining_six)
            local calls = apply_calls
            assert(boundary:before_capture(0, 0))
            assert(apply_calls == calls, 'paused parent continued applying old targets')
            boundary:handle(command(0, 4, remaining_zero))
            assert(last_sent(5) == le(4, 8) .. session .. remaining_zero)
            assert(boundary:before_capture(0, 0))
            assert(count_sent(2) == ack_count)
            assert(count_sent(4) == 0)
            assert(cells[0] == 7 and cells[1] == 6)
            calls = apply_calls
            boundary:handle(command(0, 2, 'partial_six')) -- delayed obsolete command
            assert(apply_calls == calls and cells[0] == 7)
            resolve_partial = true
            assert(boundary:before_capture(0, 0))
            applied_ack(4)
            assert(cells[0] == 7 and cells[1] == 0)
            """
        )

    def test_unpaused_overlapping_generation_faults_before_replacing_required_work(self):
        fixture().execute(
            """
            reset(1)
            boundary:handle(command(0, 2, 'partial_air'))
            local ack_count = count_sent(2)
            boundary:handle(command(0, 3, 'six'))
            assert(count_sent(4) == 1 and count_sent(2) == ack_count)
            assert(not boundary:before_capture(0, 0))
            assert(cells[0] == 0, 'unpaused replacement wrote before reporting the protocol fault')
            """
        )

    def test_pause_after_parent_completed_reports_empty_without_replaying_its_target(self):
        fixture().execute(
            """
            reset(1)
            boundary:handle(command(0, 2, 'partial_air'))
            resolve_partial = true
            assert(boundary:before_capture(0, 0))
            applied_ack(2)
            cells[1] = 6 -- legitimate edit after this old target completed
            local calls = apply_calls
            pause(3, 2)
            assert(last_sent(6) == le(3, 8) .. session, 'completed parent was copied into a child mask')
            assert(apply_calls == calls and cells[1] == 6)
            boundary:handle(command(0, 4, 'sparse_new'))
            applied_ack(4)
            assert(cells[1] == 6 and cells[2] == 3)
            assert(boundary:before_capture(0, 0))
            """
        )

    def test_partial_progress_precedes_capture_and_unchanged_mask_does_not_spam_receipts(self):
        fixture().execute(
            """
            reset(1)
            boundary:handle(command(0, 2, 'partial_air'))
            local ack_count = count_sent(2)
            assert(last_sent(5) == le(2, 8) .. session .. remaining_zero)
            assert(events[#events] == 'send:5', 'partial receipt did not precede later capture')
            assert(count_sent(5) == 1 and count_sent(2) == ack_count)
            cells[0] = 7
            for i = 1, 16 do
                assert(boundary:before_capture(0, 0), 'partial mask blanket-blocked completed-cell capture')
                events[#events + 1] = 'capture'
                assert(cells[0] == 7, 'retry re-applied an already completed cell')
            end
            assert(count_sent(4) == 0 and count_sent(5) == 1 and count_sent(2) == ack_count)
            resolve_partial = true
            assert(boundary:before_capture(0, 0))
            applied_ack(2)
            assert(cells[0] == 7 and cells[1] == 0)
            """
        )

    def test_pending_bound_faults_visibly_and_retirement_releases_capacity(self):
        fixture().execute(
            """
            reset(1)
            loaded = false
            for i = 1, 256 do boundary:handle(command(0, i + 1, 'air', i, 0)) end
            local ack_count = count_sent(2)
            boundary:handle(command(0, 258, 'air', 257, 0))
            assert(count_sent(4) == 1 and count_sent(2) == ack_count)
            assert(not boundary:before_capture(999, 0), 'capacity overflow silently evicted pending data')
            """
        )
        fixture().execute(
            """
            reset(1)
            loaded = false
            for i = 1, 300 do
                boundary:handle(command(0, i * 2, 'air', i, 0))
                boundary:handle(command(1, i * 2 + 1, '', i, 0))
            end
            assert(count_sent(4) == 0, 'retired chunks leaked pending capacity')
            loaded = true
            assert(boundary:before_capture(0, 0))
            """
        )

    def test_wrong_session_and_world_commands_never_apply_or_acknowledge(self):
        for invalid in ("le(987654321, 4)", "session"):
            with self.subTest(session=invalid):
                fixture().execute(
                    f"""
                    reset(1)
                    local ack_count = count_sent(2)
                    local bad_world = {'world_num' if invalid != 'session' else 'world_num + 1'}
                    pcall(function() boundary:handle(command(0, 2, 'air', 0, 0, bad_world, {invalid})) end)
                    assert(apply_calls == 0 and cells[0] == 6)
                    assert(count_sent(2) == ack_count)
                    """
                )

    def test_decode_failure_latches_fault_before_later_capture_or_new_command(self):
        for data in ("throw", "partial_throw"):
            with self.subTest(outcome=data):
                fixture().execute(
                    f"""
                    reset(1)
                    local ack_count = count_sent(2)
                    pcall(function() boundary:handle(command(0, 2, '{data}')) end)
                    assert(count_sent(2) == ack_count, 'failed/partial application sent completion ACK')
                    assert(count_sent(4) == 1, 'failed application did not report a bounded fault')
                    assert(#reports >= 1)
                    for i = 1, 16 do
                        assert(not boundary:before_capture(i % 3, 0), 'fault allowed subsequent capture')
                        pcall(function() boundary:handle(command(0, i + 3, 'six')) end)
                    end
                    assert(apply_calls == 1, 'faulted stream continued applying terrain')
                    assert(count_sent(4) == 1, 'fault created an unbounded reporting loop')
                    assert(count_sent(2) == ack_count)
                    """
                )

    def test_ack_send_failure_keeps_successful_application_from_becoming_unfenced_capture(self):
        fixture().execute(
            """
            reset(1)
            local ack_count = count_sent(2)
            fail_ack = true
            pcall(function() boundary:handle(command(0, 2, 'air')) end)
            assert(cells[0] == 0 and cells[1] == 0, 'controlled application did not occur')
            assert(count_sent(2) == ack_count and count_sent(4) == 1)
            assert(not boundary:before_capture(0, 0), 'failed ACK transport allowed capture')
            """
        )

    def test_partial_progress_send_failure_or_missing_mask_latches_fault_before_capture(self):
        for setup, data in (("fail_progress = true", "partial_air"), ("", "bad_remaining"), ("", "missing_loaded")):
            with self.subTest(outcome=data):
                fixture().execute(
                    f"""
                    reset(1)
                    local ack_count = count_sent(2)
                    {setup}
                    pcall(function() boundary:handle(command(0, 2, '{data}')) end)
                    assert(count_sent(2) == ack_count and count_sent(4) == 1)
                    assert(not boundary:before_capture(0, 0), 'unreported partial state allowed capture')
                    """
                )

    def test_pause_receipt_send_failure_stays_faulted_before_any_new_capture(self):
        fixture().execute(
            """
            reset(1)
            boundary:handle(command(0, 2, 'partial_air'))
            fail_pause = true
            local ack_count = count_sent(2)
            pcall(function() pause(3, 2) end)
            assert(count_sent(6) == 0 and count_sent(4) == 1 and count_sent(2) == ack_count)
            assert(not boundary:before_capture(0, 0), 'unreported pause boundary allowed capture')
            """
        )

    def test_truncated_and_unknown_commands_cannot_create_application_ack(self):
        for packet in ("''", "string.rep('x', 24)", "command(9, 2)"):
            with self.subTest(packet=packet):
                fixture().execute(
                    f"""
                    reset(1)
                    local ack_count = count_sent(2)
                    pcall(function() boundary:handle({packet}) end)
                    assert(apply_calls == 0 and count_sent(2) == ack_count)
                    assert(not boundary:before_capture(0, 0), 'invalid command did not fail closed')
                    """
                )

    def test_world_transition_starts_fresh_session_boundary_without_replaying_old_work(self):
        fixture().execute(
            """
            reset(1)
            loaded = false
            boundary:handle(command(0, 2, 'air'))
            pause(3, 2)
            assert(last_sent(6) == le(3, 8) .. session .. 'air')
            local old_session = session
            world_num = 1
            boundary:begin()
            local new_hello = assert(last_sent(3))
            session = new_hello:sub(2, 5)
            assert(not boundary:before_capture(0, 0))
            pcall(function() boundary:handle(command(0, 3, 'six', 0, 0, 0, old_session)) end)
            assert(not boundary:before_capture(0, 0))
            reset(4)
            applied_ack(4)
            loaded = true
            assert(boundary:before_capture(0, 0))
            assert(cells[0] == 6, 'old-world deferred removal was replayed')
            """
        )


if __name__ == "__main__":
    unittest.main()
