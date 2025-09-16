-- Initialize BF-CBOM database
-- This script runs automatically when the PostgreSQL container starts

-- Create additional databases if needed
-- CREATE DATABASE bf_cbom_test;

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Create initial tables
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    language VARCHAR(100),
    framework VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS crypto_libraries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    version VARCHAR(100) NOT NULL,
    library_type VARCHAR(100) NOT NULL,
    usage_frequency VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vulnerabilities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    cve_id VARCHAR(50),
    severity VARCHAR(20) NOT NULL,
    description TEXT NOT NULL,
    affected_library VARCHAR(255) NOT NULL,
    affected_versions TEXT[],
    mitigation TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    library_name VARCHAR(255) NOT NULL,
    operation VARCHAR(100) NOT NULL,
    throughput_ops_per_sec DECIMAL(12,2),
    latency_ms DECIMAL(10,3),
    memory_usage_mb DECIMAL(10,2),
    cpu_usage_percent DECIMAL(5,2),
    test_duration_sec DECIMAL(8,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);
CREATE INDEX IF NOT EXISTS idx_projects_url ON projects(url);
CREATE INDEX IF NOT EXISTS idx_crypto_libraries_project_id ON crypto_libraries(project_id);
CREATE INDEX IF NOT EXISTS idx_crypto_libraries_name ON crypto_libraries(name);
CREATE INDEX IF NOT EXISTS idx_vulnerabilities_project_id ON vulnerabilities(project_id);
CREATE INDEX IF NOT EXISTS idx_vulnerabilities_cve_id ON vulnerabilities(cve_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_results_library_name ON benchmark_results(library_name);
CREATE INDEX IF NOT EXISTS idx_benchmark_results_operation ON benchmark_results(operation);

-- Insert some sample data
INSERT INTO projects (name, url, language, framework) VALUES 
    ('Sample Web App', 'https://github.com/example/webapp', 'Python', 'FastAPI'),
    ('Crypto Library', 'https://github.com/example/cryptolib', 'Python', 'None')
ON CONFLICT DO NOTHING;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO bf_cbom;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO bf_cbom;