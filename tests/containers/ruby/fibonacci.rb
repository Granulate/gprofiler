def fibonacci( n )
    return  n  if ( 0..1 ).include? n
    ( fibonacci( n - 1 ) + fibonacci( n - 2 ) )
end

for $i in 0..200
    puts fibonacci( $i )
    STDOUT.flush
end
