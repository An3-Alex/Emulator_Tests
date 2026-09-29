param(
    [Parameter(Mandatory=$true)][string]$Assembly,
    [string]$Type = 'MainForm',
    [string[]]$Method = @('method_1','method_2','backgroundWorker_0_DoWork','button_go_Click','method_0')
)

$ErrorActionPreference = 'Stop'
$loaded = [Reflection.Assembly]::LoadFile((Resolve-Path -LiteralPath $Assembly))
$module = $loaded.ManifestModule
$flags = [Reflection.BindingFlags]'Public,NonPublic,Instance,Static,DeclaredOnly'
$targetType = $loaded.GetType($Type, $true)
$oneByte = @{}
$twoByte = @{}
[Reflection.Emit.OpCodes].GetFields([Reflection.BindingFlags]'Public,Static') | ForEach-Object {
    $opcode = [Reflection.Emit.OpCode]$_.GetValue($null)
    $value = [int]$opcode.Value -band 0xFFFF
    if (($value -band 0xFF00) -eq 0xFE00) { $twoByte[$value -band 0xFF] = $opcode }
    else { $oneByte[$value -band 0xFF] = $opcode }
}

function Read-Int32([byte[]]$bytes, [ref]$offset) {
    $value = [BitConverter]::ToInt32($bytes, $offset.Value)
    $offset.Value += 4
    return $value
}

foreach ($name in $Method) {
    if ($name -eq '.ctor') {
        $member = $targetType.GetConstructors($flags) | Select-Object -First 1
    } else {
        $member = $targetType.GetMethod($name, $flags)
    }
    if (-not $member) { throw "Method not found: $Type.$name" }
    $body = $member.GetMethodBody()
    if (-not $body) { continue }
    $bytes = $body.GetILAsByteArray()
    Write-Output "METHOD $($targetType.FullName).$name token=0x$($member.MetadataToken.ToString('X8'))"
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $instructionOffset = $offset
        $first = $bytes[$offset++]
        if ($first -eq 0xFE) { $opcode = $twoByte[[int]$bytes[$offset++]] }
        else { $opcode = $oneByte[[int]$first] }
        if (-not $opcode.Name) { throw "Unknown opcode at IL_$('{0:X4}' -f $instructionOffset)" }
        $operand = ''
        switch ($opcode.OperandType.ToString()) {
            'InlineNone' {}
            'ShortInlineI' {
                $operand = [int]$bytes[$offset]
                if ($operand -ge 128) { $operand -= 256 }
                $offset += 1
            }
            'InlineI' { $operand = Read-Int32 $bytes ([ref]$offset) }
            'InlineI8' { $operand = [BitConverter]::ToInt64($bytes,$offset); $offset += 8 }
            'ShortInlineR' { $operand = [BitConverter]::ToSingle($bytes,$offset); $offset += 4 }
            'InlineR' { $operand = [BitConverter]::ToDouble($bytes,$offset); $offset += 8 }
            'ShortInlineVar' { $operand = $bytes[$offset]; $offset += 1 }
            'InlineVar' { $operand = [BitConverter]::ToUInt16($bytes,$offset); $offset += 2 }
            'ShortInlineBrTarget' {
                $delta = [int]$bytes[$offset]
                if ($delta -ge 128) { $delta -= 256 }
                $offset += 1
                $operand = "IL_$('{0:X4}' -f ($offset + $delta))"
            }
            'InlineBrTarget' {
                $delta = Read-Int32 $bytes ([ref]$offset)
                $operand = "IL_$('{0:X4}' -f ($offset + $delta))"
            }
            'InlineSwitch' {
                $count = Read-Int32 $bytes ([ref]$offset)
                $base = $offset + 4 * $count
                $targets = for ($i=0; $i -lt $count; $i++) {
                    $delta = Read-Int32 $bytes ([ref]$offset)
                    "IL_$('{0:X4}' -f ($base + $delta))"
                }
                $operand = $targets -join ', '
            }
            default {
                $token = Read-Int32 $bytes ([ref]$offset)
                try {
                    if ($opcode.OperandType -eq 'InlineString') {
                        $resolved = '"' + $module.ResolveString($token).Replace("`r",'\r').Replace("`n",'\n') + '"'
                    } elseif ($opcode.OperandType -eq 'InlineMethod') {
                        $resolved = $module.ResolveMethod($token).ToString()
                    } elseif ($opcode.OperandType -eq 'InlineField') {
                        $resolved = $module.ResolveField($token).ToString()
                    } elseif ($opcode.OperandType -eq 'InlineType') {
                        $resolved = $module.ResolveType($token).ToString()
                    } else {
                        $resolved = $module.ResolveMember($token).ToString()
                    }
                    $operand = "0x$($token.ToString('X8')) $resolved"
                } catch { $operand = "0x$($token.ToString('X8'))" }
            }
        }
        Write-Output ("IL_{0:X4}: {1,-12} {2}" -f $instructionOffset,$opcode.Name,$operand)
    }
}
