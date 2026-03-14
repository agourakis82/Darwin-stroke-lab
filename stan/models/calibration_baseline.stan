data {
  int<lower=1> N;
  vector[N] logit_score;
  array[N] int<lower=0, upper=1> label;
}

parameters {
  real alpha;
  real beta;
}

model {
  alpha ~ normal(0, 2);
  beta ~ normal(1, 1);
  label ~ bernoulli_logit(alpha + beta * logit_score);
}

generated quantities {
  vector[N] calibrated_probability;
  for (n in 1:N) {
    calibrated_probability[n] = inv_logit(alpha + beta * logit_score[n]);
  }
}
