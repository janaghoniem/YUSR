allprojects {
    repositories {
        google()
        mavenCentral() // FIXED: Changed from mainCentral() to mavenCentral()
    }
}

val newBuildDir: Directory = rootProject.layout.buildDirectory.dir("../../build").get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
    
    // Improved fix for the "Namespace not specified" error
    afterEvaluate {
        if (project.hasProperty("android")) {
            val android = project.extensions.findByName("android")
            if (android is com.android.build.gradle.BaseExtension) {
                // If the namespace is missing, provide it specifically for this plugin
                if (android.namespace == null && project.name.contains("move_to_background")) {
                    android.namespace = "com.s10h.move_to_background"
                }

                android.compileOptions {
                    sourceCompatibility = JavaVersion.VERSION_17
                    targetCompatibility = JavaVersion.VERSION_17
                }
            }
        }
    }
    
    tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {
        kotlinOptions {
            jvmTarget = "17"
        }
    }
}

subprojects {
    project.evaluationDependsOn(":app")
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}