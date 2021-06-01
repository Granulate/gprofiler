<?php
function Fibonacci($number){

    if ($number == 0)
        return 0;
    else if ($number == 1)
        return 1;

    else
        return (Fibonacci($number-1) +
                Fibonacci($number-2));
}

$number = 200;
for ($counter = 0; $counter < $number; $counter++){
    echo Fibonacci($counter) . PHP_EOL;
}
?>
