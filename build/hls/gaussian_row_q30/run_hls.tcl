set here [file dirname [file normalize [info script]]]
cd $here
if {[file exists "$here/continuous_project"]} {error "Preserve existing HLS results"}
open_project "$here/continuous_project"
set_top gaussian_row_q30
add_files "$here/gaussian_row_q30.cpp" -cflags "-I$here"
add_files -tb "$here/test_gaussian_row.cpp" -cflags "-I$here"
add_files -tb "$here/fixtures.bin"
open_solution solution1 -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default
csim_design
csynth_design
cosim_design -rtl verilog -tool xsim -trace_level none
export_design -format ip_catalog -vendor steel.local -library hls -version 2.1
puts "STEEL_STAGE_PASS=gaussian"
exit
