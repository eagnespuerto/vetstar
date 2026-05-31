#!/usr/bin/env pwsh
# Renders the LaTeX equations used in docs/SCIENCE.md to PNGs via the
# CodeCogs online LaTeX renderer. Re-run if equations change.

$ErrorActionPreference = "Stop"
$outDir = Join-Path $PSScriptRoot "images\equations"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$equations = @{
    "transit_depth"        = '\delta \;=\; \left(\dfrac{R_{p}}{R_{\star}}\right)^{2}'
    "integrated_snr"       = '\mathrm{SNR}_{\mathrm{int}} \;=\; \dfrac{\bar{\delta}\,\sqrt{N_{\mathrm{in}}}}{\sigma_{\mathrm{out}}}'
    "mad"                  = '\sigma_{\mathrm{out}} \;=\; 1.4826 \cdot \mathrm{median}\!\left(\bigl|x_{i}-\mathrm{median}(x)\bigr|\right)'
    "adaptive_threshold"   = 'T_{\mathrm{eff}} \;=\; \min\!\bigl(T_{\mathrm{abs}},\; 1 - k\,\sigma_{\mathrm{out}}\bigr)'
    "bls_sde"              = '\mathrm{SDE} \;=\; \dfrac{\mathrm{SR}_{\mathrm{peak}} - \langle\mathrm{SR}\rangle}{\mathrm{std}(\mathrm{SR})}'
    "ls_power"             = 'P_{\mathrm{LS}}(\omega) \;=\; \dfrac{1}{2\sigma^{2}}\!\left[\dfrac{\left(\sum_{i}(x_i-\bar x)\cos\omega(t_i-\tau)\right)^{2}}{\sum_{i}\cos^{2}\omega(t_i-\tau)} + \dfrac{\left(\sum_{i}(x_i-\bar x)\sin\omega(t_i-\tau)\right)^{2}}{\sum_{i}\sin^{2}\omega(t_i-\tau)}\right]'
    "phase_fold"           = '\varphi(t) \;=\; \mathrm{mod}\!\left(\dfrac{t - t_{0}}{P},\;1\right) - \tfrac{1}{2}'
    "btjd_bjd"             = '\mathrm{BJD} \;=\; \mathrm{BTJD} \;+\; 2{,}457{,}000'
    "crowdsap"             = '\delta_{\mathrm{true}} \;=\; \dfrac{\delta_{\mathrm{obs}}}{\mathrm{CROWDSAP}}'
    "odd_even_sigma"       = 'n_\sigma \;=\; \dfrac{\bigl|\delta_{\mathrm{odd}} - \delta_{\mathrm{even}}\bigr|}{\sqrt{\sigma_{\mathrm{odd}}^{2} + \sigma_{\mathrm{even}}^{2}}}'
    "centroid_sigma"       = 'n_\sigma^{(c)} \;=\; \dfrac{\sqrt{\Delta x^{2} + \Delta y^{2}}}{\sigma_{\mathrm{centroid}}}'
    "tlcm_aRs"             = '\dfrac{a}{R_{\star}} \;\approx\; \dfrac{P}{\pi\,T_{14}}\sqrt{(1+k)^{2} - b^{2}\bigl(1-\sin^{2}\!\left(\tfrac{\pi T_{14}}{P}\right)\bigr)}'
    "seager_density"       = '\rho_{\star} \;=\; \dfrac{3\pi}{G\,P^{2}}\,\left(\dfrac{a}{R_{\star}}\right)^{3}'
    "kepler_third"         = 'a^{3} \;=\; \dfrac{G\,M_{\star}\,P^{2}}{4\pi^{2}}'
    "instellation"         = '\dfrac{S}{S_{\oplus}} \;=\; \left(\dfrac{T_{\mathrm{eff}}}{T_{\odot}}\right)^{4}\!\left(\dfrac{215.03}{a/R_{\star}}\right)^{2}'
    "teq"                  = 'T_{\mathrm{eq}} \;=\; 278.3\;\left(\dfrac{S}{S_{\oplus}}\right)^{1/4}\;\mathrm{K}'
    "kopparapu_seff"       = 'S_{\mathrm{eff}}(T_{\mathrm{eff}}) \;=\; S_{\mathrm{eff},\odot} + a\,T_{\star} + b\,T_{\star}^{2} + c\,T_{\star}^{3} + d\,T_{\star}^{4},\quad T_{\star} \equiv T_{\mathrm{eff}} - 5780\,\mathrm{K}'
    "hz_au"                = 'd_{\mathrm{HZ}}\,[\mathrm{AU}] \;=\; \sqrt{\dfrac{L_{\star}/L_{\odot}}{S_{\mathrm{eff}}}}'
    "rv_k"                 = 'K \;=\; \dfrac{203.255\;\mathrm{m\,s^{-1}}}{\sqrt{1-e^{2}}}\;\left(\dfrac{1\;\mathrm{day}}{P}\right)^{\!1/3}\!\left(\dfrac{M_{p}\sin i}{M_{\mathrm{Jup}}}\right)\!\left(\dfrac{M_{\odot}}{M_{\star}}\right)^{\!2/3}'
    "mass_function"        = 'f(m) \;=\; \dfrac{K^{3}\,P\,(1-e^{2})^{3/2}}{2\pi G} \;=\; \dfrac{(M_{p}\sin i)^{3}}{(M_{\star} + M_{p})^{2}}'
    "chen_kipping"         = 'M_{p}/M_{\oplus} \;=\; \begin{cases} (R_{p}/R_{\oplus})^{3.58}, & R_{p} < 1.23\,R_{\oplus} \\ 1.436\,(R_{p}/R_{\oplus})^{1.70}, & 1.23 \le R_{p}/R_{\oplus} < 14.3 \\ \cdots & \mathrm{(Neptunian,\,Jovian\,branches)} \end{cases}'
    "bulk_density"         = '\rho_{p} \;=\; \dfrac{3\,M_{p}}{4\pi R_{p}^{3}}'
    "luminosity"           = 'L_{\star} \;=\; 4\pi R_{\star}^{2}\,\sigma_{\mathrm{SB}}\,T_{\mathrm{eff}}^{4}'
    "hci_weighted"         = '\mathrm{HCI} \;=\; 100 \cdot \sum_{i=1}^{6} w_{i}\,s_{i},\qquad \sum_{i=1}^{6} w_{i} = 1'
    "asinh_stretch"        = 'y \;=\; \dfrac{\sinh^{-1}\!\bigl((x-x_{\mathrm{lo}})/\beta\bigr)}{\sinh^{-1}\!\bigl((x_{\mathrm{hi}}-x_{\mathrm{lo}})/\beta\bigr)},\quad y \in [0,1]'
    "exominer_binning"     = '\bar{f}_{j} \;=\; \mathrm{median}\!\bigl\{\,f_{i} : \varphi_{i} \in [\varphi_{j}, \varphi_{j+1})\,\bigr\}'
    "duration_match"       = '\bigl|T_{14}^{(s)} - T_{14}^{(s'')}\bigr| \;\le\; 0.05\;\mathrm{h}'
    "companion_radius"     = 'R_{p} \;=\; k \cdot R_{\star} \;=\; \sqrt{\delta_{\mathrm{true}}}\,\cdot R_{\star}'
}

$base = "https://latex.codecogs.com/png.image?%5Cdpi%7B160%7D%5Cbg_white%20"

foreach ($name in $equations.Keys) {
    $tex = $equations[$name]
    $encoded = [System.Uri]::EscapeDataString($tex)
    $url  = $base + $encoded
    $out  = Join-Path $outDir "$name.png"
    Write-Host "  rendering $name..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 30
    } catch {
        Write-Warning "  failed $name : $_"
    }
}
Write-Host "Done. Files in $outDir"
