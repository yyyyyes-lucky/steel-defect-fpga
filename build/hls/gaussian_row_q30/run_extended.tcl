set here [file dirname [file normalize [info script]]]
cd $here
if {[file exists "$here/extended_project"]} {error "Preserve existing validation"}
open_project "$here/extended_project"
set_top gaussian_row_q30
add_files "$here/gaussian_row_q30.cpp" -cflags "-I$here"
add_files -tb "$here/test_extended.cpp" -cflags "-I$here"
open_solution solution1 -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default
csim_design
puts "STEEL_STAGE_PASS=gaussian_extended"
exit
