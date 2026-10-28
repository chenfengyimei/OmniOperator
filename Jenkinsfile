// Windows Jenkins job 使用 Gitee SCM checkout；仓库内配置不代表已经接通 CI 服务。
pipeline {
    agent { label 'windows' }
    options {
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
        skipDefaultCheckout()
    }
    stages {
        stage('Checkout') { steps { checkout scm } }
        stage('Offline verification') {
            steps { bat 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\ci.ps1' }
        }
    }
    post {
        always {
            junit allowEmptyResults: true, testResults: '.pytest_cache/ci/pytest.xml'
            archiveArtifacts allowEmptyArchive: true, artifacts: '.pytest_cache/ci/**/report.json,.pytest_cache/ci/**/*.xml,.pytest_cache/ci/**/*.log'
        }
    }
}
