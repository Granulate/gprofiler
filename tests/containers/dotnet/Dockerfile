FROM  mcr.microsoft.com/dotnet/sdk@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22

WORKDIR /app
 
ADD Fibonacci.cs /app
ENV DOTNET_CLI_HOME = "/tmp"
RUN dotnet new console
RUN rm Program.cs
CMD ["dotnet", "run", "Fibonacci.cs"]
