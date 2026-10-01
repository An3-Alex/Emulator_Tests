/* IDirect3DDevice9 pass-through methods. Presentation methods live in the
 * owning wrapper; rendering/resource operations remain SwiftShader's. */
    HRESULT STDMETHODCALLTYPE TestCooperativeLevel() override
      { return inner_->TestCooperativeLevel(); }
    UINT STDMETHODCALLTYPE GetAvailableTextureMem() override
      { return inner_->GetAvailableTextureMem(); }
    HRESULT STDMETHODCALLTYPE EvictManagedResources() override
      { return inner_->EvictManagedResources(); }
    HRESULT STDMETHODCALLTYPE GetDeviceCaps(D3DCAPS9* pCaps) override
      { return inner_->GetDeviceCaps(pCaps); }
    HRESULT STDMETHODCALLTYPE GetDisplayMode(UINT iSwapChain,D3DDISPLAYMODE* pMode) override
      { return inner_->GetDisplayMode(iSwapChain, pMode); }
    HRESULT STDMETHODCALLTYPE SetCursorProperties(UINT XHotSpot,UINT YHotSpot,IDirect3DSurface9* pCursorBitmap) override
      { return inner_->SetCursorProperties(XHotSpot, YHotSpot, pCursorBitmap); }
    void STDMETHODCALLTYPE SetCursorPosition(int X,int Y,DWORD Flags) override
      { inner_->SetCursorPosition(X, Y, Flags); }
    BOOL STDMETHODCALLTYPE ShowCursor(BOOL bShow) override
      { return inner_->ShowCursor(bShow); }
    HRESULT STDMETHODCALLTYPE GetSwapChain(UINT iSwapChain,IDirect3DSwapChain9** pSwapChain) override
      { return inner_->GetSwapChain(iSwapChain, pSwapChain); }
    UINT STDMETHODCALLTYPE GetNumberOfSwapChains() override
      { return inner_->GetNumberOfSwapChains(); }
    HRESULT STDMETHODCALLTYPE GetBackBuffer(UINT iSwapChain,UINT iBackBuffer,D3DBACKBUFFER_TYPE Type,IDirect3DSurface9** ppBackBuffer) override
      { return inner_->GetBackBuffer(iSwapChain, iBackBuffer, Type, ppBackBuffer); }
    HRESULT STDMETHODCALLTYPE GetRasterStatus(UINT iSwapChain,D3DRASTER_STATUS* pRasterStatus) override
      { return inner_->GetRasterStatus(iSwapChain, pRasterStatus); }
    HRESULT STDMETHODCALLTYPE SetDialogBoxMode(BOOL bEnableDialogs) override
      { return inner_->SetDialogBoxMode(bEnableDialogs); }
    void STDMETHODCALLTYPE SetGammaRamp(UINT iSwapChain,DWORD Flags,CONST D3DGAMMARAMP* pRamp) override
      { inner_->SetGammaRamp(iSwapChain, Flags, pRamp); }
    void STDMETHODCALLTYPE GetGammaRamp(UINT iSwapChain,D3DGAMMARAMP* pRamp) override
      { inner_->GetGammaRamp(iSwapChain, pRamp); }
    HRESULT STDMETHODCALLTYPE CreateTexture(UINT Width,UINT Height,UINT Levels,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DTexture9** ppTexture,HANDLE* pSharedHandle) override
      { return inner_->CreateTexture(Width, Height, Levels, Usage, Format, Pool, ppTexture, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE CreateVolumeTexture(UINT Width,UINT Height,UINT Depth,UINT Levels,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DVolumeTexture9** ppVolumeTexture,HANDLE* pSharedHandle) override
      { return inner_->CreateVolumeTexture(Width, Height, Depth, Levels, Usage, Format, Pool, ppVolumeTexture, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE CreateCubeTexture(UINT EdgeLength,UINT Levels,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DCubeTexture9** ppCubeTexture,HANDLE* pSharedHandle) override
      { return inner_->CreateCubeTexture(EdgeLength, Levels, Usage, Format, Pool, ppCubeTexture, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE CreateVertexBuffer(UINT Length,DWORD Usage,DWORD FVF,D3DPOOL Pool,IDirect3DVertexBuffer9** ppVertexBuffer,HANDLE* pSharedHandle) override
      { return inner_->CreateVertexBuffer(Length, Usage, FVF, Pool, ppVertexBuffer, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE CreateIndexBuffer(UINT Length,DWORD Usage,D3DFORMAT Format,D3DPOOL Pool,IDirect3DIndexBuffer9** ppIndexBuffer,HANDLE* pSharedHandle) override
      { return inner_->CreateIndexBuffer(Length, Usage, Format, Pool, ppIndexBuffer, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE CreateRenderTarget(UINT Width,UINT Height,D3DFORMAT Format,D3DMULTISAMPLE_TYPE MultiSample,DWORD MultisampleQuality,BOOL Lockable,IDirect3DSurface9** ppSurface,HANDLE* pSharedHandle) override
      { return inner_->CreateRenderTarget(Width, Height, Format, MultiSample, MultisampleQuality, Lockable, ppSurface, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE CreateDepthStencilSurface(UINT Width,UINT Height,D3DFORMAT Format,D3DMULTISAMPLE_TYPE MultiSample,DWORD MultisampleQuality,BOOL Discard,IDirect3DSurface9** ppSurface,HANDLE* pSharedHandle) override
      { return inner_->CreateDepthStencilSurface(Width, Height, Format, MultiSample, MultisampleQuality, Discard, ppSurface, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE UpdateSurface(IDirect3DSurface9* pSourceSurface,CONST RECT* pSourceRect,IDirect3DSurface9* pDestinationSurface,CONST POINT* pDestPoint) override
      { return inner_->UpdateSurface(pSourceSurface, pSourceRect, pDestinationSurface, pDestPoint); }
    HRESULT STDMETHODCALLTYPE UpdateTexture(IDirect3DBaseTexture9* pSourceTexture,IDirect3DBaseTexture9* pDestinationTexture) override
      { return inner_->UpdateTexture(pSourceTexture, pDestinationTexture); }
    HRESULT STDMETHODCALLTYPE GetRenderTargetData(IDirect3DSurface9* pRenderTarget,IDirect3DSurface9* pDestSurface) override
      { return inner_->GetRenderTargetData(pRenderTarget, pDestSurface); }
    HRESULT STDMETHODCALLTYPE GetFrontBufferData(UINT iSwapChain,IDirect3DSurface9* pDestSurface) override
      { return inner_->GetFrontBufferData(iSwapChain, pDestSurface); }
    HRESULT STDMETHODCALLTYPE StretchRect(IDirect3DSurface9* pSourceSurface,CONST RECT* pSourceRect,IDirect3DSurface9* pDestSurface,CONST RECT* pDestRect,D3DTEXTUREFILTERTYPE Filter) override
      { return inner_->StretchRect(pSourceSurface, pSourceRect, pDestSurface, pDestRect, Filter); }
    HRESULT STDMETHODCALLTYPE ColorFill(IDirect3DSurface9* pSurface,CONST RECT* pRect,D3DCOLOR color) override
      { return inner_->ColorFill(pSurface, pRect, color); }
    HRESULT STDMETHODCALLTYPE CreateOffscreenPlainSurface(UINT Width,UINT Height,D3DFORMAT Format,D3DPOOL Pool,IDirect3DSurface9** ppSurface,HANDLE* pSharedHandle) override
      { return inner_->CreateOffscreenPlainSurface(Width, Height, Format, Pool, ppSurface, pSharedHandle); }
    HRESULT STDMETHODCALLTYPE SetRenderTarget(DWORD RenderTargetIndex,IDirect3DSurface9* pRenderTarget) override
      { return inner_->SetRenderTarget(RenderTargetIndex, pRenderTarget); }
    HRESULT STDMETHODCALLTYPE GetRenderTarget(DWORD RenderTargetIndex,IDirect3DSurface9** ppRenderTarget) override
      { return inner_->GetRenderTarget(RenderTargetIndex, ppRenderTarget); }
    HRESULT STDMETHODCALLTYPE SetDepthStencilSurface(IDirect3DSurface9* pNewZStencil) override
      { return inner_->SetDepthStencilSurface(pNewZStencil); }
    HRESULT STDMETHODCALLTYPE GetDepthStencilSurface(IDirect3DSurface9** ppZStencilSurface) override
      { return inner_->GetDepthStencilSurface(ppZStencilSurface); }
    HRESULT STDMETHODCALLTYPE BeginScene() override
      { return inner_->BeginScene(); }
    HRESULT STDMETHODCALLTYPE EndScene() override
      { return inner_->EndScene(); }
    HRESULT STDMETHODCALLTYPE Clear(DWORD Count,CONST D3DRECT* pRects,DWORD Flags,D3DCOLOR Color,float Z,DWORD Stencil) override
      { return inner_->Clear(Count, pRects, Flags, Color, Z, Stencil); }
    HRESULT STDMETHODCALLTYPE SetTransform(D3DTRANSFORMSTATETYPE State,CONST D3DMATRIX* pMatrix) override
      { return inner_->SetTransform(State, pMatrix); }
    HRESULT STDMETHODCALLTYPE GetTransform(D3DTRANSFORMSTATETYPE State,D3DMATRIX* pMatrix) override
      { return inner_->GetTransform(State, pMatrix); }
    HRESULT STDMETHODCALLTYPE MultiplyTransform(D3DTRANSFORMSTATETYPE State, CONST D3DMATRIX* matrix) override
      { return inner_->MultiplyTransform(State, matrix); }
    HRESULT STDMETHODCALLTYPE SetViewport(CONST D3DVIEWPORT9* pViewport) override
      { return inner_->SetViewport(pViewport); }
    HRESULT STDMETHODCALLTYPE GetViewport(D3DVIEWPORT9* pViewport) override
      { return inner_->GetViewport(pViewport); }
    HRESULT STDMETHODCALLTYPE SetMaterial(CONST D3DMATERIAL9* pMaterial) override
      { return inner_->SetMaterial(pMaterial); }
    HRESULT STDMETHODCALLTYPE GetMaterial(D3DMATERIAL9* pMaterial) override
      { return inner_->GetMaterial(pMaterial); }
    HRESULT STDMETHODCALLTYPE SetLight(DWORD Index, CONST D3DLIGHT9* light) override
      { return inner_->SetLight(Index, light); }
    HRESULT STDMETHODCALLTYPE GetLight(DWORD Index, D3DLIGHT9* light) override
      { return inner_->GetLight(Index, light); }
    HRESULT STDMETHODCALLTYPE LightEnable(DWORD Index,BOOL Enable) override
      { return inner_->LightEnable(Index, Enable); }
    HRESULT STDMETHODCALLTYPE GetLightEnable(DWORD Index,BOOL* pEnable) override
      { return inner_->GetLightEnable(Index, pEnable); }
    HRESULT STDMETHODCALLTYPE SetClipPlane(DWORD Index,CONST float* pPlane) override
      { return inner_->SetClipPlane(Index, pPlane); }
    HRESULT STDMETHODCALLTYPE GetClipPlane(DWORD Index,float* pPlane) override
      { return inner_->GetClipPlane(Index, pPlane); }
    HRESULT STDMETHODCALLTYPE SetRenderState(D3DRENDERSTATETYPE State,DWORD Value) override
      { return inner_->SetRenderState(State, Value); }
    HRESULT STDMETHODCALLTYPE GetRenderState(D3DRENDERSTATETYPE State,DWORD* pValue) override
      { return inner_->GetRenderState(State, pValue); }
    HRESULT STDMETHODCALLTYPE CreateStateBlock(D3DSTATEBLOCKTYPE Type,IDirect3DStateBlock9** ppSB) override
      { return inner_->CreateStateBlock(Type, ppSB); }
    HRESULT STDMETHODCALLTYPE BeginStateBlock() override
      { return inner_->BeginStateBlock(); }
    HRESULT STDMETHODCALLTYPE EndStateBlock(IDirect3DStateBlock9** ppSB) override
      { return inner_->EndStateBlock(ppSB); }
    HRESULT STDMETHODCALLTYPE SetClipStatus(CONST D3DCLIPSTATUS9* pClipStatus) override
      { return inner_->SetClipStatus(pClipStatus); }
    HRESULT STDMETHODCALLTYPE GetClipStatus(D3DCLIPSTATUS9* pClipStatus) override
      { return inner_->GetClipStatus(pClipStatus); }
    HRESULT STDMETHODCALLTYPE GetTexture(DWORD Stage,IDirect3DBaseTexture9** ppTexture) override
      { return inner_->GetTexture(Stage, ppTexture); }
    HRESULT STDMETHODCALLTYPE SetTexture(DWORD Stage,IDirect3DBaseTexture9* pTexture) override
      { return inner_->SetTexture(Stage, pTexture); }
    HRESULT STDMETHODCALLTYPE GetTextureStageState(DWORD Stage,D3DTEXTURESTAGESTATETYPE Type,DWORD* pValue) override
      { return inner_->GetTextureStageState(Stage, Type, pValue); }
    HRESULT STDMETHODCALLTYPE SetTextureStageState(DWORD Stage,D3DTEXTURESTAGESTATETYPE Type,DWORD Value) override
      { return inner_->SetTextureStageState(Stage, Type, Value); }
    HRESULT STDMETHODCALLTYPE GetSamplerState(DWORD Sampler,D3DSAMPLERSTATETYPE Type,DWORD* pValue) override
      { return inner_->GetSamplerState(Sampler, Type, pValue); }
    HRESULT STDMETHODCALLTYPE SetSamplerState(DWORD Sampler,D3DSAMPLERSTATETYPE Type,DWORD Value) override
      { return inner_->SetSamplerState(Sampler, Type, Value); }
    HRESULT STDMETHODCALLTYPE ValidateDevice(DWORD* pNumPasses) override
      { return inner_->ValidateDevice(pNumPasses); }
    HRESULT STDMETHODCALLTYPE SetPaletteEntries(UINT PaletteNumber,CONST PALETTEENTRY* pEntries) override
      { return inner_->SetPaletteEntries(PaletteNumber, pEntries); }
    HRESULT STDMETHODCALLTYPE GetPaletteEntries(UINT PaletteNumber,PALETTEENTRY* pEntries) override
      { return inner_->GetPaletteEntries(PaletteNumber, pEntries); }
    HRESULT STDMETHODCALLTYPE SetCurrentTexturePalette(UINT PaletteNumber) override
      { return inner_->SetCurrentTexturePalette(PaletteNumber); }
    HRESULT STDMETHODCALLTYPE GetCurrentTexturePalette(UINT *PaletteNumber) override
      { return inner_->GetCurrentTexturePalette(PaletteNumber); }
    HRESULT STDMETHODCALLTYPE SetScissorRect(CONST RECT* pRect) override
      { return inner_->SetScissorRect(pRect); }
    HRESULT STDMETHODCALLTYPE GetScissorRect(RECT* pRect) override
      { return inner_->GetScissorRect(pRect); }
    HRESULT STDMETHODCALLTYPE SetSoftwareVertexProcessing(BOOL bSoftware) override
      { return inner_->SetSoftwareVertexProcessing(bSoftware); }
    BOOL STDMETHODCALLTYPE GetSoftwareVertexProcessing() override
      { return inner_->GetSoftwareVertexProcessing(); }
    HRESULT STDMETHODCALLTYPE SetNPatchMode(float nSegments) override
      { return inner_->SetNPatchMode(nSegments); }
    float STDMETHODCALLTYPE GetNPatchMode() override
      { return inner_->GetNPatchMode(); }
    HRESULT STDMETHODCALLTYPE DrawPrimitive(D3DPRIMITIVETYPE PrimitiveType,UINT StartVertex,UINT PrimitiveCount) override
      { return inner_->DrawPrimitive(PrimitiveType, StartVertex, PrimitiveCount); }
    HRESULT STDMETHODCALLTYPE DrawIndexedPrimitive(D3DPRIMITIVETYPE Type, INT BaseVertexIndex, UINT MinVertexIndex, UINT NumVertices, UINT startIndex, UINT primCount) override
      { return inner_->DrawIndexedPrimitive(Type, BaseVertexIndex, MinVertexIndex, NumVertices, startIndex, primCount); }
    HRESULT STDMETHODCALLTYPE DrawPrimitiveUP(D3DPRIMITIVETYPE PrimitiveType,UINT PrimitiveCount,CONST void* pVertexStreamZeroData,UINT VertexStreamZeroStride) override
      { return inner_->DrawPrimitiveUP(PrimitiveType, PrimitiveCount, pVertexStreamZeroData, VertexStreamZeroStride); }
    HRESULT STDMETHODCALLTYPE DrawIndexedPrimitiveUP(D3DPRIMITIVETYPE PrimitiveType,UINT MinVertexIndex,UINT NumVertices,UINT PrimitiveCount,CONST void* pIndexData,D3DFORMAT IndexDataFormat,CONST void* pVertexStreamZeroData,UINT VertexStreamZeroStride) override
      { return inner_->DrawIndexedPrimitiveUP(PrimitiveType, MinVertexIndex, NumVertices, PrimitiveCount, pIndexData, IndexDataFormat, pVertexStreamZeroData, VertexStreamZeroStride); }
    HRESULT STDMETHODCALLTYPE ProcessVertices(UINT SrcStartIndex,UINT DestIndex,UINT VertexCount,IDirect3DVertexBuffer9* pDestBuffer,IDirect3DVertexDeclaration9* pVertexDecl,DWORD Flags) override
      { return inner_->ProcessVertices(SrcStartIndex, DestIndex, VertexCount, pDestBuffer, pVertexDecl, Flags); }
    HRESULT STDMETHODCALLTYPE CreateVertexDeclaration(CONST D3DVERTEXELEMENT9* pVertexElements,IDirect3DVertexDeclaration9** ppDecl) override
      { return inner_->CreateVertexDeclaration(pVertexElements, ppDecl); }
    HRESULT STDMETHODCALLTYPE SetVertexDeclaration(IDirect3DVertexDeclaration9* pDecl) override
      { return inner_->SetVertexDeclaration(pDecl); }
    HRESULT STDMETHODCALLTYPE GetVertexDeclaration(IDirect3DVertexDeclaration9** ppDecl) override
      { return inner_->GetVertexDeclaration(ppDecl); }
    HRESULT STDMETHODCALLTYPE SetFVF(DWORD FVF) override
      { return inner_->SetFVF(FVF); }
    HRESULT STDMETHODCALLTYPE GetFVF(DWORD* pFVF) override
      { return inner_->GetFVF(pFVF); }
    HRESULT STDMETHODCALLTYPE CreateVertexShader(CONST DWORD* pFunction,IDirect3DVertexShader9** ppShader) override
      { return inner_->CreateVertexShader(pFunction, ppShader); }
    HRESULT STDMETHODCALLTYPE SetVertexShader(IDirect3DVertexShader9* pShader) override
      { return inner_->SetVertexShader(pShader); }
    HRESULT STDMETHODCALLTYPE GetVertexShader(IDirect3DVertexShader9** ppShader) override
      { return inner_->GetVertexShader(ppShader); }
    HRESULT STDMETHODCALLTYPE SetVertexShaderConstantF(UINT StartRegister,CONST float* pConstantData,UINT Vector4fCount) override
      { return inner_->SetVertexShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    HRESULT STDMETHODCALLTYPE GetVertexShaderConstantF(UINT StartRegister,float* pConstantData,UINT Vector4fCount) override
      { return inner_->GetVertexShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    HRESULT STDMETHODCALLTYPE SetVertexShaderConstantI(UINT StartRegister,CONST int* pConstantData,UINT Vector4iCount) override
      { return inner_->SetVertexShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    HRESULT STDMETHODCALLTYPE GetVertexShaderConstantI(UINT StartRegister,int* pConstantData,UINT Vector4iCount) override
      { return inner_->GetVertexShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    HRESULT STDMETHODCALLTYPE SetVertexShaderConstantB(UINT StartRegister,CONST BOOL* pConstantData,UINT  BoolCount) override
      { return inner_->SetVertexShaderConstantB(StartRegister, pConstantData, BoolCount); }
    HRESULT STDMETHODCALLTYPE GetVertexShaderConstantB(UINT StartRegister,BOOL* pConstantData,UINT BoolCount) override
      { return inner_->GetVertexShaderConstantB(StartRegister, pConstantData, BoolCount); }
    HRESULT STDMETHODCALLTYPE SetStreamSource(UINT StreamNumber,IDirect3DVertexBuffer9* pStreamData,UINT OffsetInBytes,UINT Stride) override
      { return inner_->SetStreamSource(StreamNumber, pStreamData, OffsetInBytes, Stride); }
    HRESULT STDMETHODCALLTYPE GetStreamSource(UINT StreamNumber,IDirect3DVertexBuffer9** ppStreamData,UINT* pOffsetInBytes,UINT* pStride) override
      { return inner_->GetStreamSource(StreamNumber, ppStreamData, pOffsetInBytes, pStride); }
    HRESULT STDMETHODCALLTYPE SetStreamSourceFreq(UINT StreamNumber,UINT Setting) override
      { return inner_->SetStreamSourceFreq(StreamNumber, Setting); }
    HRESULT STDMETHODCALLTYPE GetStreamSourceFreq(UINT StreamNumber,UINT* pSetting) override
      { return inner_->GetStreamSourceFreq(StreamNumber, pSetting); }
    HRESULT STDMETHODCALLTYPE SetIndices(IDirect3DIndexBuffer9* pIndexData) override
      { return inner_->SetIndices(pIndexData); }
    HRESULT STDMETHODCALLTYPE GetIndices(IDirect3DIndexBuffer9** ppIndexData) override
      { return inner_->GetIndices(ppIndexData); }
    HRESULT STDMETHODCALLTYPE CreatePixelShader(CONST DWORD* pFunction,IDirect3DPixelShader9** ppShader) override
      { return inner_->CreatePixelShader(pFunction, ppShader); }
    HRESULT STDMETHODCALLTYPE SetPixelShader(IDirect3DPixelShader9* pShader) override
      { return inner_->SetPixelShader(pShader); }
    HRESULT STDMETHODCALLTYPE GetPixelShader(IDirect3DPixelShader9** ppShader) override
      { return inner_->GetPixelShader(ppShader); }
    HRESULT STDMETHODCALLTYPE SetPixelShaderConstantF(UINT StartRegister,CONST float* pConstantData,UINT Vector4fCount) override
      { return inner_->SetPixelShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    HRESULT STDMETHODCALLTYPE GetPixelShaderConstantF(UINT StartRegister,float* pConstantData,UINT Vector4fCount) override
      { return inner_->GetPixelShaderConstantF(StartRegister, pConstantData, Vector4fCount); }
    HRESULT STDMETHODCALLTYPE SetPixelShaderConstantI(UINT StartRegister,CONST int* pConstantData,UINT Vector4iCount) override
      { return inner_->SetPixelShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    HRESULT STDMETHODCALLTYPE GetPixelShaderConstantI(UINT StartRegister,int* pConstantData,UINT Vector4iCount) override
      { return inner_->GetPixelShaderConstantI(StartRegister, pConstantData, Vector4iCount); }
    HRESULT STDMETHODCALLTYPE SetPixelShaderConstantB(UINT StartRegister,CONST BOOL* pConstantData,UINT  BoolCount) override
      { return inner_->SetPixelShaderConstantB(StartRegister, pConstantData, BoolCount); }
    HRESULT STDMETHODCALLTYPE GetPixelShaderConstantB(UINT StartRegister,BOOL* pConstantData,UINT BoolCount) override
      { return inner_->GetPixelShaderConstantB(StartRegister, pConstantData, BoolCount); }
    HRESULT STDMETHODCALLTYPE DrawRectPatch(UINT Handle,CONST float* pNumSegs,CONST D3DRECTPATCH_INFO* pRectPatchInfo) override
      { return inner_->DrawRectPatch(Handle, pNumSegs, pRectPatchInfo); }
    HRESULT STDMETHODCALLTYPE DrawTriPatch(UINT Handle,CONST float* pNumSegs,CONST D3DTRIPATCH_INFO* pTriPatchInfo) override
      { return inner_->DrawTriPatch(Handle, pNumSegs, pTriPatchInfo); }
    HRESULT STDMETHODCALLTYPE DeletePatch(UINT Handle) override
      { return inner_->DeletePatch(Handle); }
    HRESULT STDMETHODCALLTYPE CreateQuery(D3DQUERYTYPE Type,IDirect3DQuery9** ppQuery) override
      { return inner_->CreateQuery(Type, ppQuery); }
