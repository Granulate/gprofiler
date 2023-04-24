FROM apache/spark

# Runs SparkPi with 1000 as argument
CMD ["/opt/spark/bin/spark-submit", "--class", "org.apache.spark.examples.SparkPi", "--master", "local[*]", "/opt/spark/examples/jars/spark-examples_2.12-3.4.0.jar", "1000"]