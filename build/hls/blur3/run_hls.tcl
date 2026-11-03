set here [file dirname [file normalize [info script]]]
cd $here
if {[file exists "$here/blur3_project"]} {error "Preserve existing HLS results"}
open_project "$here/blur3_project"
set_top blur3
add_files "$here/blur3.cpp" -cflags "-I$here"
add_files -tb "$here/test_blur3.cpp" -cflags "-I$here"
add_files -tb "$here/fixtures.bin"
open_solution solution1 -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default
csim_design
csynth_design
cosim_design -rtl verilog -tool xsim -trace_level none
export_design -format ip_catalog -vendor steel.local -library hls -version 1.0
puts "STEEL_STAGE_PASS=blur3"
exit
