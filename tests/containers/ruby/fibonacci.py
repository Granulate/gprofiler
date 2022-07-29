def fibonacci(n):
    print("SIUR")
    print(n)
    if n==0 or n==1:
        return  n  
    ( fibonacci( n - 1 ) + fibonacci( n - 2 ) )


for i in range(0,3):
    print(fibonacci( i ))
