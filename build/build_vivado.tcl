# Rebuild the selected v10 design using Vivado 2026.1 and exported HLS IPs.
set package_root [file normalize $::env(STEEL_REPRO_ROOT)]
set v8_root [file normalize $::env(STEEL_REPRO_WORK)]
if {[version -short] ne "2026.1"} {error "Expected Vivado 2026.1, got [version -short]"}
set v5_root [file join $package_root src]
set q30_ip [file join $v8_root hls gaussian_row_q30 continuous_project solution1 impl ip]
set ellipse_ip [file join $v8_root hls ellipse7 ellipse7_project solution1 impl ip]
set blur_ip [file join $v8_root hls blur3 blur3_project solution1 impl ip]
foreach ip [list $q30_ip $ellipse_ip $blur_ip] {
    if {![file isfile "$ip/component.xml"]} {
        error "Required HLS IP export missing: $ip/component.xml. Run build.ps1 with Vitis 2026.1 vitis-run --mode hls --tcl first."
    }
}
if {[file exists "$v8_root/vivado"] || [file exists "$v8_root/hardware"]} {
    error "Build directory already exists. Preserve it; do not recreate in place."
}
if {![file isfile "$q30_ip/component.xml"]} {error "Gaussian Q30 IP is missing"}
if {![file isfile "$ellipse_ip/component.xml"]} {
    error "Ellipse IP is missing. Run hls/run_hls_export.tcl with Vitis HLS first."
}

create_project steel_gaussian_v10 "$v8_root/vivado" -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property ip_repo_paths [list $q30_ip $ellipse_ip $blur_ip] [current_project]
update_ip_catalog

set v5_import [file join $v5_root rtl]
set q8_src $v5_import
set sobel_src $v5_import
import_files -norecurse -fileset sources_1 [list \
    "$q8_src/axis_gray_unpacker_4to1.v" "$q8_src/axis_local_std_q8_interior_1px.v" \
    "$q8_src/q8_std_math.v" "$q8_src/axis_q8_padded_frame_chain_32to32.v" \
    "$sobel_src/axis_sobel.v" "$sobel_src/sobel_pixel.v"]
update_compile_order -fileset sources_1
set bd_result [source [file join $v5_root rtl dual_system_bd.tcl]]
if {[string is integer -strict $bd_result] && $bd_result != 0} {error "Base block design creation failed: $bd_result"}

# Recreate the verified Gaussian branch used by steel_triple.
create_bd_cell -type ip -vlnv steel.local:hls:gaussian_row_q30:2.1 gaussian_row_q30_0
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_dark
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {26} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32}] [get_bd_cells axi_dma_dark]
set_property CONFIG.NUM_MI {6} [get_bd_cells axi_smc]
set_property CONFIG.NUM_SI {8} [get_bd_cells axi_mem_intercon]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M02_AXI] [get_bd_intf_pins axi_dma_dark/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M03_AXI] [get_bd_intf_pins gaussian_row_q30_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins axi_dma_dark/M_AXI_MM2S] [get_bd_intf_pins axi_mem_intercon/S04_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_dark/M_AXI_S2MM] [get_bd_intf_pins axi_mem_intercon/S05_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_dark/M_AXIS_MM2S] [get_bd_intf_pins gaussian_row_q30_0/input_r]
connect_bd_intf_net [get_bd_intf_pins gaussian_row_q30_0/output_r] [get_bd_intf_pins axi_dma_dark/S_AXIS_S2MM]

# Add the independent 7x7 ellipse accelerator for the binary candidate.
create_bd_cell -type ip -vlnv steel.local:hls:ellipse7:1.0 ellipse7_0
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_ellipse
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {26} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32}] [get_bd_cells axi_dma_ellipse]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M04_AXI] [get_bd_intf_pins axi_dma_ellipse/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M05_AXI] [get_bd_intf_pins ellipse7_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins axi_dma_ellipse/M_AXI_MM2S] [get_bd_intf_pins axi_mem_intercon/S06_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_ellipse/M_AXI_S2MM] [get_bd_intf_pins axi_mem_intercon/S07_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_ellipse/M_AXIS_MM2S] [get_bd_intf_pins ellipse7_0/input_r]
connect_bd_intf_net [get_bd_intf_pins ellipse7_0/output_r] [get_bd_intf_pins axi_dma_ellipse/S_AXIS_S2MM]

foreach pin {axi_dma_dark/s_axi_lite_aclk axi_dma_dark/m_axi_mm2s_aclk axi_dma_dark/m_axi_s2mm_aclk gaussian_row_q30_0/ap_clk \
             axi_dma_ellipse/s_axi_lite_aclk axi_dma_ellipse/m_axi_mm2s_aclk axi_dma_ellipse/m_axi_s2mm_aclk ellipse7_0/ap_clk \
             axi_mem_intercon/S04_ACLK axi_mem_intercon/S05_ACLK axi_mem_intercon/S06_ACLK axi_mem_intercon/S07_ACLK} {
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins $pin]
}
foreach pin {axi_dma_dark/axi_resetn gaussian_row_q30_0/ap_rst_n axi_mem_intercon/S04_ARESETN axi_mem_intercon/S05_ARESETN \
             axi_dma_ellipse/axi_resetn ellipse7_0/ap_rst_n axi_mem_intercon/S06_ARESETN axi_mem_intercon/S07_ARESETN} {
    connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] [get_bd_pins $pin]
}
foreach {cell offset segment} {axi_dma_dark 0x40420000 S_AXI_LITE/Reg \
    gaussian_row_q30_0 0x43C00000 s_axi_control/Reg \
    axi_dma_ellipse 0x40430000 S_AXI_LITE/Reg \
    ellipse7_0 0x43C10000 s_axi_control/Reg} {
    assign_bd_address -offset $offset -range 64K -target_address_space \
        [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs $cell/$segment] -force
}
foreach dma_space {Data_MM2S Data_S2MM} {
    assign_bd_address -offset 0 -range 512M -target_address_space \
        [get_bd_addr_spaces axi_dma_ellipse/$dma_space] [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
}
foreach dma_space {Data_MM2S Data_S2MM} {
    assign_bd_address -offset 0 -range 512M -target_address_space \
        [get_bd_addr_spaces axi_dma_dark/$dma_space] [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
}


# Add blur3 without removing any existing branch or changing old addresses.
create_bd_cell -type ip -vlnv steel.local:hls:blur3:1.0 blur3_0
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_blur3
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {26} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32}] [get_bd_cells axi_dma_blur3]
set_property CONFIG.NUM_MI {8} [get_bd_cells axi_smc]
set_property CONFIG.NUM_SI {10} [get_bd_cells axi_mem_intercon]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M06_AXI] [get_bd_intf_pins axi_dma_blur3/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M07_AXI] [get_bd_intf_pins blur3_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins axi_dma_blur3/M_AXI_MM2S] [get_bd_intf_pins axi_mem_intercon/S08_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_blur3/M_AXI_S2MM] [get_bd_intf_pins axi_mem_intercon/S09_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_blur3/M_AXIS_MM2S] [get_bd_intf_pins blur3_0/input_r]
connect_bd_intf_net [get_bd_intf_pins blur3_0/output_r] [get_bd_intf_pins axi_dma_blur3/S_AXIS_S2MM]
foreach pin {axi_dma_blur3/s_axi_lite_aclk axi_dma_blur3/m_axi_mm2s_aclk axi_dma_blur3/m_axi_s2mm_aclk blur3_0/ap_clk axi_mem_intercon/S08_ACLK axi_mem_intercon/S09_ACLK} {
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins $pin]
}
foreach pin {axi_dma_blur3/axi_resetn blur3_0/ap_rst_n axi_mem_intercon/S08_ARESETN axi_mem_intercon/S09_ARESETN} {
    connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] [get_bd_pins $pin]
}
foreach {cell offset segment} {axi_dma_blur3 0x40440000 S_AXI_LITE/Reg blur3_0 0x43C20000 s_axi_control/Reg} {
    assign_bd_address -offset $offset -range 64K -target_address_space [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs $cell/$segment] -force
}
foreach dma_space {Data_MM2S Data_S2MM} {
    assign_bd_address -offset 0 -range 512M -target_address_space [get_bd_addr_spaces axi_dma_blur3/$dma_space] [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
}

validate_bd_design
save_bd_design
write_bd_tcl "$v8_root/gaussian_v10_system_bd.tcl"
generate_target all [get_files system.bd]
add_files -norecurse [make_wrapper -files [get_files system.bd] -top]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
puts "GAUSSIAN_V10_BD_VALIDATION_PASS"
if {[info exists ::env(STEEL_REPRO_STAGE)] && $::env(STEEL_REPRO_STAGE) eq "ValidateBD"} {
    close_project
    exit 0
}
file mkdir "$v8_root/reports"
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {![string match "*Complete*" [get_property STATUS [get_runs synth_1]]]} {error "Synthesis failed"}
launch_runs impl_1 -to_step route_design -jobs 2
wait_on_run impl_1
if {![string match "*Complete*" [get_property STATUS [get_runs impl_1]]]} {error "Implementation failed"}
open_run impl_1
report_utilization -file "$v8_root/reports/gaussian_v10_utilization.rpt"
report_timing_summary -file "$v8_root/reports/gaussian_v10_timing.rpt"
report_drc -file "$v8_root/reports/gaussian_v10_drc.rpt"
set fatal_drc [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]
if {[llength $fatal_drc] > 0} {error "Unresolved design-rule errors/critical warnings; export skipped: $fatal_drc"}
set setup_paths [get_timing_paths -delay_type max -max_paths 1]
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {[llength $setup_paths] == 0 || [llength $hold_paths] == 0} {error "Missing timing paths"}
set wns [get_property SLACK $setup_paths]
set whs [get_property SLACK $hold_paths]
puts "GAUSSIAN_V10_WNS=$wns GAUSSIAN_V10_WHS=$whs"
if {$wns < 0 || $whs < 0} {error "Timing failed; export skipped"}
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {![string match "*Complete*" [get_property STATUS [get_runs impl_1]]]} {error "Bitstream failed"}
set run_dir [get_property DIRECTORY [get_runs impl_1]]
set bit "$run_dir/system_wrapper.bit"
set hwh "$v8_root/vivado/steel_gaussian_v10.gen/sources_1/bd/system/hw_handoff/system.hwh"
if {![file isfile $bit] || ![file isfile $hwh]} {error "Missing bit/hwh"}
file mkdir "$v8_root/hardware"
file copy $bit "$v8_root/hardware/steel_gaussian_v10.bit"
file copy $hwh "$v8_root/hardware/steel_gaussian_v10.hwh"
puts "GAUSSIAN_V10_BUILD_AND_EXPORT_PASS"
close_project
