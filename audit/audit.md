# UKRDC Stats Quality Assurance
Reusable UKRDC cohort generation requires as much quality assurance as is practical. The validation process for this code has three parts.

# Code Audit Reporting
Big-picture statistical accuracy testing provides independent comparisons, referencing both the publicly published UKRR aggregate data and unpublished internal, unaggregated data.

# Automated Unit Testing
The repo should have good unit test coverage of its functions.

# Automated Edge Case Testing
Very specific, story-based automated tests arise from edge cases and make the code robust against messy real-world data.

# (Experimental) Synthetic data testing 
The repo now uses pandera schemas this creates some new posibilities like monte carloing the code as the logic branches. 