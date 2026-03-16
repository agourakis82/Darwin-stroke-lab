CREATE DATABASE coder OWNER coder;
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
CREATE DATABASE lab_runs OWNER lab_runs;

GRANT ALL PRIVILEGES ON DATABASE coder TO coder;
GRANT ALL PRIVILEGES ON DATABASE temporal TO temporal;
GRANT ALL PRIVILEGES ON DATABASE temporal_visibility TO temporal;
GRANT ALL PRIVILEGES ON DATABASE lab_runs TO lab_runs;
