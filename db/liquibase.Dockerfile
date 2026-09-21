FROM liquibase/liquibase
ADD https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar /liquibase/lib/postgresql.jar
USER root
RUN chmod 644 /liquibase/lib/postgresql.jar
USER liquibase
