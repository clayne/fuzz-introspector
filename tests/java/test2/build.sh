#!/bin/sh

rm -f ./*.class
rm -f ./*.jar
javac -cp ../jazzer_api_deploy.jar *.java
jar cfv test2.jar *.class
rm -f ./*.class