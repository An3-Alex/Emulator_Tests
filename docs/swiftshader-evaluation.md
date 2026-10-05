# Direct3D crash investigation

The four-serial-port run reached InitDx after `SoundEngine erstellt`.
`logs/four-serial-game-diagnostics-run1/LogFiles/logDatei.txt` reports
`D3DERR_NOTAVAILABLE` in baseDx9.cpp line 532. The displayed crash at
0x00691209 reads address zero in BaseDx9::StartDrawToBackBuff. This supports
a failed Direct3D device initialization followed by a null dereference.

## Evaluation candidate

SwiftShader DX9 SM3 demo build 3383, October 2010, obtained from:
https://www.vogonsdrivers.com/wrappers/files/Direct3D/Software/SwiftShader/SwiftShader%203.0%20DX9/SwiftShader_DX9_SM3_Build_3383.zip

- ZIP SHA256: 58AA1A7BAD73969128D347077E4CC7365EA1F9F98FE31C3A4D80CA4261AF660F
- x86 d3d9.dll SHA256: 7C1934EB90B41AC4B6D0590ABFFEF69AB01DE5AA917F1630892174AEB537DD0C
- PE32 machine 0x014c; OS/subsystem 5.1; linker 10.0.
- 165 imports from USER32, GDI32, PSAPI, KERNEL32, WS2_32, DDRAW and dbghelp.
- Supplied README explicitly lists XP and requires SSE2.
- This is a demonstration/evaluation binary with a logo overlay and restrictive
  redistribution terms; it is not the final redistributable project renderer.
  Preserve the supplied license and README. Do not include this binary in a
  distributed project bundle.

`scripts/install_swiftshader_eval.sh` installs the checked DLL into NVRAM
and WorkDir only, refusing unknown existing app-local d3d9.dll files.
System Direct3D files are not replaced. Rollback consists of removing only
these two app-local DLLs after verifying their recorded hash, with QEMU stopped.

`test-swiftshader.ps1` uses WHPX, explicit SSE2, one QXL device with
`max_outputs=1`, four serial ports and restricted user networking. Guest
reboots remain enabled.
Runtime success is pending; PE inspection alone does not prove compatibility.

## First runtime result

The test completed a 180-second two-display capture in
`captures/swiftshader-run2`. Both displays remained black at the end;
the earlier 0x00691209 dialog did not appear in the inspected frames.
QEMU was then closed through QMP and logs extracted read-only to
`logs/swiftshader-run1`.

The new game log no longer reports `D3DERR_NOTAVAILABLE` or the resolution
retry. It progresses from TFT1 dimensions at baseDx9.cpp lines 102/103 to
TFT2 dimensions at lines 225/226, then contains no later entries.
This is progress beyond the previous failure, not proof that initialization
completed. Next investigation: second-display device/swap-chain creation
and any blocking operation after line 226. COM configuration warning 126
remains. The VM is stopped; evaluation DLLs remain installed.

An adapter-index-0 experiment made the second CreateDevice return instead of
hanging, but it returned D3DERR_INVALIDCALL and triggered the resolution retry.
That experiment was superseded. Static analysis of the caller at 0x006E0D63
showed an existing fallback: when only one display adapter is reported, it
changes display mode 1 to mode 2 before calling InitDx. Mode 2 uses one D3D
device and the game's two-TFT render-target path. The single-D3D test patch
NOPs only the conditional branch at 0x006E0D72 so that the existing fallback
also activates with QXL's two Windows display devices and SwiftShader's one
D3D adapter. The adapter-index patch is absent from this replacement binary.

The first single-D3D runtime widened the game window and then crashed inside
relocated SwiftShader build 3383 at 0x0208A046 while reading 0xFFFFFFFF.
The game log stopped after TFT1 width/height, before InitDx returned. Build
3383 is therefore superseded for this image by build 5003. Its archive hash is
5F4451C96815A4416957FF5CD9C577AD88A10FD657606CFC6EF892A46AA58D32 and
its x86 DLL hash is FC5994B209A57A77275E5ECEE1904CD9139A344C69E221E54F05AF90580A90C9.
The PE header targets machine 0x014C and OS/subsystem 5.1; its supplied README
explicitly lists Windows XP. Runtime compatibility remains to be tested.

## Original executable and external adapter mapping

The checksum-covered original `game.exe` imports only `Direct3DCreate9` from
`d3d9.dll`. In the post-auto-chkdsk run it created the null audio engine,
logged both logical 800x600 TFT dimensions, and then stopped returning log
entries at the second Direct3D initialization path. Both app-local renderer
files still had the verified build-5003 hash, so this is not evidence that
SwiftShader was absent.

`src/d3d9_proxy.cpp` is an XP/x86 app-local COM proxy. It loads the verified
renderer as `swiftshader_d3d9.dll`, forwards the complete `IDirect3D9`
interface, logs `Direct3DCreate9` and `CreateDevice`, and maps only logical
adapter 1 to renderer adapter 0. This externalizes the behavior of the former
one-byte executable experiment while keeping both original game executables at
SHA-256 `27C4553927397B1E8443D6CAEA12E5B4E7282E4948B67B85C80C4DB0D1D0427D`.
The first diagnostic proxy hash was
`801EB42C6AF73542ECB290F4844BF6DDAB5A9A5DAF2C3A963A66583188FD06D3`.
It imports only XP-era `KERNEL32.dll` functions and targets PE32 machine 0x014C,
OS/subsystem 5.1. Runtime verification is in progress.

The first proxy run proved that `Direct3DCreate9` and the first fullscreen
`CreateDevice(adapter=0)` succeeded. The original application then requested a
second fullscreen device on logical adapter 1. Mapping that request to adapter
0 entered SwiftShader but never returned, establishing that the black screen
was a second-exclusive-device conflict rather than a missing renderer. Static
inspection then corrected the topology model: `0x006D46B0` counts active child
monitors, not just PCI display functions. The next run therefore caps the QXL
ROM at one monitor output so the original executable can select mode 2 before
Direct3D initialization.

Because the required final layout has two visible QEMU tabs, the current proxy
keeps two QXL PCI devices and changes only the second SwiftShader device request
from exclusive fullscreen to windowed mode before mapping adapter 1 to 0. The
first device remains exclusive and unchanged. Current reproducible proxy hash:
`31D2D484D4821EF34DD764E68A66338ED638926C66B73B14078D360713F4987F`.
