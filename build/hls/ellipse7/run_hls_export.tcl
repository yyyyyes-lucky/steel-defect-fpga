set here [file dirname [file normalize [info script]]]
cd $here
if {[file exists [file join $here ellipse7_project]]} {
    error "Refuse to reset an existing HLS project; preserve previous results"
}
open_project [file join $here ellipse7_project]
set_top ellipse7
add_files [file join $here ellipse7.cpp] -cflags "-I$here"
add_files -tb [file join $here test_ellipse7.cpp] -cflags "-I$here"
add_files -tb [file join $here fixtures.bin]
open_solution solution1 -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default
csim_design
csynth_design
cosim_design -rtl verilog -tool xsim -trace_level none
export_design -format ip_catalog -vendor steel.local -library hls -version 1.0
puts "STEEL_STAGE_PASS=ellipse7"
exit
