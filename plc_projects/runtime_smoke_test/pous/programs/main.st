PROGRAM main
  VAR
    cycle_counter : DINT := 0;
    mb_command : UINT AT %MW0 := 0;
    mb_response : UINT AT %MW1 := 0;
  END_VAR

cycle_counter := cycle_counter + 1;
mb_response := mb_command + 1;

END_PROGRAM