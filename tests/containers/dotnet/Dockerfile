FROM  mcr.microsoft.com/dotnet/sdk@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22

WORKDIR /app
 
ADD Fibonacci.dll /app
ADD Fibonacci.deps.json /app
ADD Fibonacci.runtimeconfig.json /app

CMD ["dotnet", "Fibonacci.dll"]
