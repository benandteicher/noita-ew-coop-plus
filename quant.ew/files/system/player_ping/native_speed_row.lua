-- Pure relocation builder for the verified native Spread-row clone.
-- Generated offline from instruction boundaries with Capstone 5.0.9.
-- No native machine-code block is distributed: the manager supplies its own
-- verified game bytes and checks BLOCK_SHA256 before calling build().
-- This module does not read/write process memory, install hooks or call FFI.
local M={
    BLOCK_RVA=0x76aa84, BLOCK_END_RVA=0x76af45, BLOCK_LENGTH=0x4c1,
    BLOCK_SHA256="204600f4a992309079dfc437055cb7d0b8e2aaa69fc5f6fbce4862201bd34400",
    BLOCK_HEADER=string.char(0x8d,0x4d,0x9c,0xe8,0x24,0xe1,0x8c,0xff),
    INSERT_RVA=0x76af45, INSERT_BYTES=string.char(0x8b,0x85,0x5c,0xbf,0xff,0xff),
    -- This exact block contains only the latter two widget-ID immediates.
    HAS_4CF3_IMMEDIATE=false,
}
local PREFERRED_BASE,IMAGE_SIZE,U32=0x400000,0xee9000,4294967296
-- {source offset, source byte length, optional operation/target/condition}.
-- Raw spans end at every branch destination or replacement boundary.
local rows={
    {0x0,0x3},
    {0x3,0x5,"call",0x38bb0},
    {0x8,0x1c},
    {0x24,0x5,"neutral"},
    {0x29,0x5},
    {0x2e,0x5,"call",0x396b0},
    {0x33,0x3},
    {0x36,0x2,"jcc",0x76aad8,0x5},
    {0x38,0x1a},
    {0x52,0x2,"jmp",0x76ab10},
    {0x54,0x3},
    {0x57,0x2,"jcc",0x76aaf9,0x5},
    {0x59,0x1a},
    {0x73,0x2,"jmp",0x76ab10},
    {0x75,0x17},
    {0x8c,0x7},
    {0x93,0x5,"call",0x2b6e0},
    {0x98,0x7},
    {0x9f,0x5,"call",0x495100},
    {0xa4,0x5,"ptr","icon_prefix"},
    {0xa9,0x6},
    {0xaf,0x5,"call",0x1cc80},
    {0xb4,0x5,"ptr","icon_name"},
    {0xb9,0xc},
    {0xc5,0x5,"call",0x410b0},
    {0xca,0x14},
    {0xde,0x5,"call",0x410b0},
    {0xe3,0x1a},
    {0xfd,0x5,"call",0x39920},
    {0x102,0xe},
    {0x110,0x5,"call",0x39930},
    {0x115,0x3e},
    {0x153,0x5,"call",0x420b70},
    {0x158,0xa},
    {0x162,0x5,"call",0x429150},
    {0x167,0x9},
    {0x170,0x5,"call",0x1cce0},
    {0x175,0x6},
    {0x17b,0x5,"call",0x1cce0},
    {0x180,0xa},
    {0x18a,0x5,"call",0x1cce0},
    {0x18f,0x5,"ptr","label"},
    {0x194,0x6},
    {0x19a,0x5,"call",0x1cc80},
    {0x19f,0x6},
    {0x1a5,0x5,"call",0x2c340},
    {0x1aa,0x1c},
    {0x1c6,0x5,"call",0x39920},
    {0x1cb,0xe},
    {0x1d9,0x5,"call",0x39930},
    {0x1de,0xb},
    {0x1e9,0x5,"call",0x396b0},
    {0x1ee,0x29},
    {0x217,0x5,"id",0x4cf5,0x7ed2},
    {0x21c,0x7},
    {0x223,0x5,"call",0x421630},
    {0x228,0xa},
    {0x232,0x5,"call",0x429150},
    {0x237,0x9},
    {0x240,0x5,"call",0x2c360},
    {0x245,0xa},
    {0x24f,0x5,"call",0x1cce0},
    {0x254,0x5,"ptr","empty"},
    {0x259,0x6},
    {0x25f,0x5,"call",0x1cc80},
    {0x264,0x6},
    {0x26a,0x5,"call",0x2c340},
    {0x26f,0x12},
    {0x281,0x2e,"text"},
    {0x2af,0x1c},
    {0x2cb,0x5,"call",0x39920},
    {0x2d0,0xe},
    {0x2de,0x5,"call",0x39930},
    {0x2e3,0x2f},
    {0x312,0x5,"id",0x4cf7,0x7ed3},
    {0x317,0x9},
    {0x320,0x5,"call",0x421630},
    {0x325,0xa},
    {0x32f,0x5,"call",0x429150},
    {0x334,0x9},
    {0x33d,0x5,"call",0x1cce0},
    {0x342,0x6},
    {0x348,0x5,"call",0x1cce0},
    {0x34d,0x6},
    {0x353,0x5,"call",0x2c360},
    {0x358,0xa},
    {0x362,0x5,"call",0x1cce0},
    {0x367,0x39},
    {0x3a0,0x2,"jcc",0x76ae5c,0x4},
    {0x3a2,0x5,"ptr","help"},
    {0x3a7,0x6},
    {0x3ad,0x5,"call",0x1cc80},
    {0x3b2,0x14},
    {0x3c6,0x5,"call",0x39620},
    {0x3cb,0x2},
    {0x3cd,0x2,"jcc",0x76ae62,0x5},
    {0x3cf,0x7},
    {0x3d6,0x2,"jmp",0x76ae69},
    {0x3d8,0x6},
    {0x3de,0x7},
    {0x3e5,0x6},
    {0x3eb,0x2,"jcc",0x76ae88,0x4},
    {0x3ed,0x12},
    {0x3ff,0x5,"call",0x1cce0},
    {0x404,0x7},
    {0x40b,0x6,"jcc",0x76af45,0x4},
    {0x411,0xb},
    {0x41c,0x5,"call",0x1cc80},
    {0x421,0xf},
    {0x430,0x5,"call",0x1cc80},
    {0x435,0xf},
    {0x444,0x5,"call",0x1cc80},
    {0x449,0x5,"ptr","help"},
    {0x44e,0xa},
    {0x458,0x5,"call",0x1cc80},
    {0x45d,0x2c},
    {0x489,0x5,"call",0x4291b0},
    {0x48e,0x9},
    {0x497,0x5,"call",0x1cce0},
    {0x49c,0x6},
    {0x4a2,0x5,"call",0x1cce0},
    {0x4a7,0x6},
    {0x4ad,0x5,"call",0x1cce0},
    {0x4b2,0xa},
    {0x4bc,0x5,"call",0x1cce0},
}
-- PE HIGHLOW relocation sites: {block offset, target RVA}.
local absolute_sites={
    {0x3c,0xc5361c},
    {0x4a,0xc53780},
    {0x5d,0xc5361c},
    {0x6b,0xc53780},
    {0x79,0xc53780},
    {0xa5,0xc3a524},
    {0xb5,0xbf8998},
    {0xce,0xc08d88},
    {0xf6,0xc54088},
    {0x190,0xc3a7b8},
    {0x1ba,0xc540ec},
    {0x1c2,0xc53cf8},
    {0x255,0xc3a7a4},
    {0x2c7,0xc540ec},
    {0x37a,0xd5331c},
    {0x397,0xc53d24},
    {0x3a3,0xc3ad38},
    {0x412,0xbe480c},
    {0x422,0xbe480c},
    {0x436,0xbe3c84},
    {0x44a,0xc3ad38},
}

local function integer(n,lo,hi)
    return type(n)=="number" and n==n and n>=lo and n<=hi and n%1==0
end
local function check(ok,reason)
    if not ok then error("native speed row: "..reason,3) end
end
local function bytes32(n)
    n=n%U32
    return string.char(n%256,math.floor(n/256)%256,math.floor(n/65536)%256,math.floor(n/16777216)%256)
end
local function read32(s,offset)
    local a,b,c,d=s:byte(offset+1,offset+4)
    check(d~=nil,"truncated source word")
    return a+b*256+c*65536+d*16777216
end
local function replace(s,offset,value)
    return s:sub(1,offset)..value..s:sub(offset+#value+1)
end
local function branch(source,offset,length,kind,target,condition)
    local first,second=source:byte(offset+1,offset+2)
    local displacement
    if kind=="call" then
        check(first==0xe8 and length==5,"call opcode mismatch")
    elseif kind=="jmp" then
        check((first==0xe9 and length==5)or(first==0xeb and length==2),"jump opcode mismatch")
    else
        check((length==2 and first==0x70+condition)
            or(length==6 and first==0x0f and second==0x80+condition),"condition opcode mismatch")
    end
    if length==2 then
        displacement=second
        if displacement>=128 then displacement=displacement-256 end
    else
        displacement=read32(source,offset+length-4)
        if displacement>=2147483648 then displacement=displacement-U32 end
    end
    check((M.BLOCK_RVA+offset+length+displacement)%U32==target,"source branch target mismatch")
end
function M.build(codeAddress,imageBase,originalBlock,ptrs)
    check(integer(codeAddress,0x10000,U32-1),"invalid code address")
    check(integer(imageBase,0x10000,U32-IMAGE_SIZE),"invalid image base")
    check(type(originalBlock)=="string" and #originalBlock==M.BLOCK_LENGTH,"incorrect block length")
    check(originalBlock:sub(1,#M.BLOCK_HEADER)==M.BLOCK_HEADER,"block header mismatch")
    check(type(ptrs)=="table" and getmetatable(ptrs)==nil,"invalid pointer table")
    for _,key in ipairs({"icon_prefix","icon_name","label","help","empty"}) do
        check(integer(ptrs[key],1,U32-1),"invalid "..key.." pointer")
    end
    -- Accept sealed disk bytes or the same game block already relocated to this
    -- imageBase. Normalize every absolute image reference exactly once.
    local source=originalBlock
    for _,site in ipairs(absolute_sites) do
        local actual=read32(source,site[1])
        local disk,loaded=PREFERRED_BASE+site[2],imageBase+site[2]
        check(actual==disk or actual==loaded,"absolute relocation mismatch")
        source=replace(source,site[1],bytes32(loaded))
    end
    local mapped,total={},0
    for _,row in ipairs(rows) do
        local offset,length,kind=row[1],row[2],row[3]
        mapped[offset]=total
        local size=length
        if kind=="call"or kind=="jmp"then
            branch(source,offset,length,kind,row[4]);size=5
        elseif kind=="jcc"then
            branch(source,offset,length,kind,row[4],row[5]);size=6
        elseif kind=="neutral"then
            branch(source,offset,length,"call",0x75f620);size=5
        elseif kind=="text"then
            check(source:sub(offset+1,offset+5)==string.char(0xba,1,0,0,0),"formatter prefix mismatch")
            branch(source,offset+0x10,5,"call",0x75f060)
            branch(source,offset+0x26,5,"call",0x44ae90);size=22
        elseif kind=="ptr"then
            check(source:byte(offset+1)==0x68 and length==5,"string pointer opcode mismatch")
        elseif kind=="id"then
            check(source:byte(offset+1)==0x68 and length==5 and read32(source,offset+1)==row[4],"widget ID mismatch")
        end
        total=total+size
    end
    -- The exclusive source end is a real cloned label. In particular the JE at
    -- RVA 0x76ae8f must reach this label, never the original injection address.
    mapped[M.BLOCK_LENGTH]=total
    check(codeAddress+total<=U32,"code range wraps address space")
    local function target(rva)
        if rva>=M.BLOCK_RVA and rva<=M.BLOCK_END_RVA then
            local offset=mapped[rva-M.BLOCK_RVA]
            check(offset~=nil,"internal target is not a cloned label")
            return codeAddress+offset
        end
        return imageBase+rva
    end
    local out={}
    for _,row in ipairs(rows) do
        local offset,length,kind=row[1],row[2],row[3]
        local address=codeAddress+mapped[offset]
        local emitted
        if kind=="call"or kind=="jmp"then
            emitted=string.char(kind=="call"and 0xe8 or 0xe9)..bytes32(target(row[4])-(address+5))
        elseif kind=="jcc"then
            emitted=string.char(0x0f,0x80+row[5])..bytes32(target(row[4])-(address+6))
        elseif kind=="neutral"then emitted=string.char(0xb8)..bytes32(1)
        elseif kind=="ptr"then emitted=string.char(0x68)..bytes32(ptrs[row[4]])
        elseif kind=="id"then emitted=string.char(0x68)..bytes32(row[5])
        elseif kind=="text"then
            -- Construct both locals directly. The original localized-suffix
            -- formatter assumes a nonempty "$key" and cannot accept our empty
            -- suffix. Preserve its EH state and final result local without
            -- invoking either numeric conversion or localized formatting.
            -- Constructors are thiscall, return ECX in EAX and remove one arg.
            emitted=string.char(0x53,0xe8)..bytes32(imageBase+0x1cc80-(address+6))
                ..string.char(0xc6,0x45,0xfc,0x98,0x53,0x8d,0x8d,0xfc,0xec,0xff,0xff,0xe8)
                ..bytes32(imageBase+0x1cc80-(address+22))
        else emitted=source:sub(offset+1,offset+length) end
        out[#out+1]=emitted
    end
    return table.concat(out)
end
return M
