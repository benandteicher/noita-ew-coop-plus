-- Main-thread application/capture fence. ACK and samples share one FIFO stream.
local M = { MAX_PENDING_CHUNKS = 256 }
local function u32(n)
    n = n % 4294967296
    local out = {}
    for i = 1, 4 do out[i] = string.char(n % 256); n = math.floor(n / 256) end
    return table.concat(out)
end
local function read32(s, at)
    local a,b,c,d = string.byte(s, at, at + 3)
    return a + b*256 + c*65536 + d*16777216
end
local function signed(n) return n >= 2147483648 and n - 4294967296 or n end

function M.new(adapter)
    local self = { session = 0, pending = {}, count = 0, waiting = true, fault = nil, last = nil }
    local function fail(reason)
        -- util.tpcall catches handler errors. Latch before reporting so capture
        -- cannot run after a partially applied packet or a failed ACK send.
        self.fault = tostring(reason)
        self.pending = {}; self.count = 0
        pcall(adapter.send, 4, string.sub(self.fault, 1, 512))
        if adapter.report then adapter.report(self.fault) end
        return false
    end
    local function receipt(key, token, data)
        local ok, err = pcall(adapter.send, key, token .. u32(self.session) .. (data or ""))
        if not ok then return fail(err) end
        return true
    end
    local function ack(token) return receipt(2, token) end
    local function apply(command, initial)
        local ok, complete, remaining, loaded = pcall(adapter.apply, command.data, command.cx, command.cy)
        if not ok then return fail(complete) end
        if type(loaded) ~= "boolean" then return fail("Terrain decoder v2 loaded result required") end
        if complete == true then
            if remaining ~= nil then return fail("Completed terrain retained required cells") end
            if not ack(command.token) then return false, false end
            return true, loaded
        end
        if type(remaining) ~= "string" or #remaining == 0 or #remaining > 81932 then
            return fail("Terrain decoder did not return a bounded remaining mask")
        end
        -- Initial receipt enables an explicit PAUSE handshake. Later unchanged
        -- eligibility checks do not retransmit or retry any constructor/removal.
        if initial or remaining ~= command.data then
            if not receipt(5, command.token, remaining) then return false, false end
        end
        command.data = remaining
        return false, loaded
    end
    function self:begin()
        if self.session == 4294967295 then return fail("Terrain session exhausted") end
        self.session = self.session + 1
        self.pending = {}; self.count = 0; self.waiting = true; self.last = nil
        -- A fault is cleared only by a new world/session initialization.
        self.fault = nil
        local ok, err = pcall(adapter.send, 3, string.char(2) .. u32(self.session) .. u32(adapter.world()))
        if not ok then return fail(err) end
        return true
    end
    function self:stop(reason) return fail(reason) end
    function self:handle(bytes)
        if self.fault then return false end
        if #bytes < 25 then return fail("Truncated terrain command") end
        local kind = string.byte(bytes,1)
        local token = string.sub(bytes,2,9)
        if signed(read32(bytes,10)) ~= adapter.world() or read32(bytes,14) ~= self.session then return false end
        -- Proxy emits monotonic tokens in stream order; duplicates cannot
        -- replay a successful application over a subsequent simulation edit.
        local order = string.reverse(token)
        if self.last and order <= self.last then return false end
        if kind ~= 2 and self.waiting then return fail("Terrain application before session reset") end
        self.last = order
        if kind == 2 then
            if #bytes ~= 25 then return fail("Invalid terrain reset") end
            self.pending = {}; self.count = 0
            if not ack(token) then return false end
            self.waiting = false
            return true
        end
        local cx, cy = signed(read32(bytes,18)), signed(read32(bytes,22))
        local key = cx .. ":" .. cy
        if kind == 1 then
            if #bytes ~= 25 then return fail("Invalid terrain retirement") end
            if self.pending[key] then self.pending[key] = nil; self.count = self.count - 1 end
            return ack(token)
        end
        if kind == 3 then
            if #bytes ~= 33 then return fail("Invalid terrain pause") end
            local pending = self.pending[key]
            if pending then
                if pending.token ~= string.sub(bytes,26,33) then return fail("Terrain pause application mismatch") end
                -- Freeze before reporting. No old application can complete
                -- between this receipt and the replacement generation.
                pending.paused = true
            end
            return receipt(6, token, pending and pending.data or "")
        end
        if kind ~= 0 then return fail("Unknown terrain command") end
        if self.pending[key] then
            if not self.pending[key].paused then return fail("Unpaused terrain application replacement") end
            self.pending[key] = nil; self.count = self.count - 1
        end
        if self.count >= M.MAX_PENDING_CHUNKS then return fail("Terrain application capacity exceeded") end
        local command = {token=token,data=string.sub(bytes,26),cx=cx,cy=cy}
        if apply(command, true) then return true end
        if self.fault then return false end
        self.pending[key] = command; self.count = self.count + 1
        return false
    end
    function self:before_capture(cx, cy)
        if self.fault or self.waiting then return false end
        local key = cx .. ":" .. cy
        local command = self.pending[key]
        if command then
            if command.paused then return true end
            -- Normal capture checks only retained cells. A physical solid is
            -- read-only until it vacates; successful cells never reappear here.
            local complete, loaded = apply(command, false)
            if self.fault then return false end
            if complete then self.pending[key] = nil; self.count = self.count - 1 end
            return loaded
        end
        return true
    end
    return self
end
return M
